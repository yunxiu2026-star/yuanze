"""Private, versioned enterprise documents and explicit review workflow."""
from contextlib import contextmanager
from datetime import date,datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import unicodedata
import uuid

from .knowledge_extract import EXTENSIONS,MAX_FILE,MAX_TEXT

STATES={'draft':'草稿','pending':'待审核','approved':'已审核','changes':'需修改','archived':'已归档'}
DEFAULT_CATEGORIES=['企业与工厂','产品与配方','OEM / ODM 服务','包装与交付','质量与认证','商务与报价','常见问题','其他资料']
EXTRACTION_LOCK=threading.Lock()
class KnowledgeError(ValueError):pass

def now():return datetime.now(timezone.utc).isoformat()
def clean(value,limit,required=False):
    value=str(value or '').strip()
    if len(value)>limit or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',value):raise KnowledgeError('内容过长或含有不支持的控制字符。')
    if required and not value:raise KnowledgeError('请填写必填内容。')
    return value
def folded(value):return unicodedata.normalize('NFKC',value).casefold()
def file_name(value):
    name=str(value or '').replace('\\','/').rsplit('/',1)[-1]
    name=clean(name,180,True)
    if Path(name).suffix.lower() not in EXTENSIONS:raise KnowledgeError('支持 PDF、Word（DOCX）、Excel（XLSX）、TXT、Markdown 和 CSV。')
    return name

def extract_file(data,filename):
    name=file_name(filename)
    if not data or len(data)>MAX_FILE:raise KnowledgeError('文件不能为空，单个文件不超过 10 MB。')
    if not EXTRACTION_LOCK.acquire(blocking=False):raise KnowledgeError('有资料正在解析，请稍后重试。')
    try:
        proc=subprocess.run([sys.executable,'-m','geo_monitor.web.knowledge_extract',Path(name).suffix.lower()],
            input=data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,check=False)
        if proc.returncode or len(proc.stdout)>2*1024*1024:raise KnowledgeError('解析未完成，文件可能过于复杂；请拆分资料或另存为文字文档。')
        result=json.loads(proc.stdout)
        if not result.get('ok'):raise KnowledgeError(result.get('error','无法解析文件。'))
        return dict(filename=name,digest=hashlib.sha256(data).hexdigest(),size=len(data),**result)
    except (subprocess.TimeoutExpired,ValueError) as exc:
        if isinstance(exc,KnowledgeError):raise
        raise KnowledgeError('解析超时或文件内容不支持，请拆分资料或另存后重试。') from None
    finally:EXTRACTION_LOCK.release()

class KnowledgeStore:
    def __init__(self):
        self.root=Path(os.getenv('KNOWLEDGE_DATA_DIR','data/knowledge-base')).resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.files=self.root/'files';self.files.mkdir(exist_ok=True,mode=0o700)
        self.path=self.root/'knowledge.db'

    @contextmanager
    def database(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY,name TEXT NOT NULL,key TEXT NOT NULL UNIQUE,version INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,current_revision INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'draft',version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reviewer TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',reviewed_at TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id),number INTEGER NOT NULL,title TEXT NOT NULL,category_id INTEGER NOT NULL REFERENCES categories(id),tags TEXT NOT NULL,summary TEXT NOT NULL,body TEXT NOT NULL,source TEXT NOT NULL,effective_date TEXT NOT NULL,filename TEXT NOT NULL,digest TEXT NOT NULL,bytes INTEGER NOT NULL,extraction_note TEXT NOT NULL,search_text TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(document_id,number));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,document_id INTEGER REFERENCES documents(id),at TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS revisions_document ON revisions(document_id);''')
            if db.execute('SELECT COUNT(*) FROM categories').fetchone()[0]==0:
                db.executemany('INSERT INTO categories(name,key) VALUES(?,?)',[(x,folded(x)) for x in DEFAULT_CATEGORIES]);db.commit()
            yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()

    def event(self,db,id,action,**detail):db.execute('INSERT INTO events(document_id,at,action,details) VALUES(?,?,?,?)',(id,now(),action,json.dumps(detail,ensure_ascii=False)))
    def categories(self):
        with self.database() as db:return [dict(r) for r in db.execute('SELECT c.*,COUNT(r.id) AS documents FROM categories c LEFT JOIN revisions r ON r.category_id=c.id AND r.id IN (SELECT current_revision FROM documents) GROUP BY c.id ORDER BY c.id')]
    def category(self,name,id=None,version=None):
        name=clean(name,60,True)
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM categories WHERE key=? AND id!=?',(folded(name),id or 0)).fetchone():raise KnowledgeError('分类名称已存在。')
            if id:
                if db.execute('UPDATE categories SET name=?,key=?,version=version+1 WHERE id=? AND version=?',(name,folded(name),id,version)).rowcount!=1:raise KnowledgeError('分类已更新，请刷新后重试。')
            else:id=db.execute('INSERT INTO categories(name,key) VALUES(?,?)',(name,folded(name))).lastrowid
            self.event(db,None,'分类更名' if version else '新增分类',category_id=id,name=name)
            return id

    def fields(self,raw):
        result={k:clean(raw.get(k,''),n,k=='title') for k,n in dict(title=160,tags=300,summary=2000,body=MAX_TEXT,source=500,effective_date=10).items()}
        try:result['category_id']=int(raw.get('category_id','0'))
        except ValueError:raise KnowledgeError('请选择资料分类。') from None
        if result['effective_date']:
            try:date.fromisoformat(result['effective_date'])
            except ValueError:raise KnowledgeError('资料日期格式不正确。') from None
        return result

    def _read(self,db,id):
        r=db.execute('SELECT d.*,r.number,r.title,r.category_id,r.tags,r.summary,r.body,r.source,r.effective_date,r.filename,r.digest,r.bytes,r.extraction_note,c.name AS category FROM documents d JOIN revisions r ON r.id=d.current_revision JOIN categories c ON c.id=r.category_id WHERE d.id=?',(id,)).fetchone()
        return dict(r) if r else None
    def get(self,id):
        with self.database() as db:return self._read(db,id)
    def history(self,id):
        with self.database() as db:
            return [dict(r,details=json.loads(r['details'])) for r in db.execute('SELECT * FROM events WHERE document_id=? ORDER BY id DESC',(id,))]
    def revisions(self,id):
        with self.database() as db:return [dict(r) for r in db.execute('SELECT id,number,title,filename,created_at FROM revisions WHERE document_id=? ORDER BY number DESC',(id,))]
    def revision(self,id,revision):
        with self.database() as db:
            r=db.execute('SELECT r.*,c.name AS category FROM revisions r JOIN categories c ON c.id=r.category_id WHERE r.document_id=? AND r.id=?',(id,revision)).fetchone()
            return dict(r) if r else None

    def save(self,raw,attachment=None,data=None,id=None,version=None):
        fields=self.fields(raw)
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE');old=self._read(db,id) if id else None
            if id and (not old or old['version']!=version):raise KnowledgeError('资料已更新，请刷新后核对，避免覆盖他人的修改。')
            if old and old['status'] in {'pending','archived'}:raise KnowledgeError('请先撤回审核或恢复归档，再编辑资料。')
            if not db.execute('SELECT id FROM categories WHERE id=?',(fields['category_id'],)).fetchone():raise KnowledgeError('请选择有效分类。')
            if attachment:
                if data is None or hashlib.sha256(data).hexdigest()!=attachment['digest']:raise KnowledgeError('文件校验失败，请重新上传。')
                duplicate=db.execute('SELECT d.id FROM documents d JOIN revisions r ON r.id=d.current_revision WHERE r.digest=? AND d.id!=?',(attachment['digest'],id or 0)).fetchone()
                if duplicate:raise KnowledgeError(f"该文件已存在于资料 #{duplicate['id']}，请打开原资料查看或修订。")
                f={k:attachment[k] for k in ['filename','digest','size']};f['bytes']=f.pop('size');f['extraction_note']=attachment['warning']
                if not fields['body']:fields['body']=attachment['body']
                target=self.files/(f['digest']+'.blob')
                if not target.exists():
                    temporary=self.files/(uuid.uuid4().hex+'.pending')
                    try:
                        with temporary.open('xb') as output:output.write(data)
                        temporary.chmod(0o600);os.replace(temporary,target)
                    finally:temporary.unlink(missing_ok=True)
            elif old:f={k:old[k] for k in ['filename','digest','bytes','extraction_note']}
            else:raise KnowledgeError('请选择需要上传的资料。')
            stamp=now()
            if not id:id=db.execute('INSERT INTO documents(current_revision,created_at,updated_at) VALUES(0,?,?)',(stamp,stamp)).lastrowid
            number=(old['number']+1) if old else 1
            search=folded(' '.join(str(v) for v in [*fields.values(),f['filename']]))
            values=dict(document_id=id,number=number,**fields,**f,search_text=search,created_at=stamp)
            revision=db.execute('INSERT INTO revisions('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values())).lastrowid
            db.execute("UPDATE documents SET current_revision=?,status='draft',version=version+?,updated_at=?,reviewer='',review_note='',reviewed_at='' WHERE id=?",(revision,int(bool(old)),stamp,id))
            self.event(db,id,'保存新版本' if old else '上传资料',revision=number,filename=f['filename'])
            return id

    def transition(self,id,version,action,reviewer='',note=''):
        reviewer=clean(reviewer,80);note=clean(note,2000)
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE');old=self._read(db,id)
            if not old or old['version']!=version:raise KnowledgeError('资料状态已更新，请刷新后重试。')
            targets={'submit':('draft','changes'),'withdraw':('pending',),'approve':('pending',),'return':('pending',),'archive':('draft','changes','approved'),'restore':('archived',)}
            if action not in targets or old['status'] not in targets[action]:raise KnowledgeError('当前状态不能执行此操作。')
            if action in {'submit','approve'} and not old['body'].strip():raise KnowledgeError('请先补充可检索正文，再提交或通过审核。')
            if action in {'approve','return'} and (not reviewer or not note):raise KnowledgeError('请填写审核人及审核意见。')
            if action=='archive' and not note:raise KnowledgeError('请填写归档原因。')
            status={'submit':'pending','withdraw':'draft','approve':'approved','return':'changes','archive':'archived','restore':'draft'}[action]
            stamp=now();db.execute('UPDATE documents SET status=?,version=version+1,updated_at=?,reviewer=?,review_note=?,reviewed_at=? WHERE id=?',
                (status,stamp,reviewer if action in {'approve','return'} else '',note if action in {'approve','return'} else '',stamp if action in {'approve','return'} else '',id))
            self.event(db,id,{'submit':'提交审核','withdraw':'撤回审核','approve':'审核通过','return':'退回修改','archive':'归档资料','restore':'恢复为草稿'}[action],revision=old['number'],reviewer=reviewer,note=note)

    def search(self,q='',category='',status='approved',page=1):
        q=clean(q,200)
        if status!='all' and status not in STATES:raise KnowledgeError('资料状态无效。')
        where=[];args=[]
        if status!='all':where.append('d.status=?');args.append(status)
        if category:
            try:category=int(category)
            except ValueError:raise KnowledgeError('资料分类无效。') from None
            where.append('r.category_id=?');args.append(category)
        for term in folded(q).split()[:12]:
            where.append("r.search_text LIKE ? ESCAPE '\\'");args.append('%'+term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        clause=' WHERE '+' AND '.join(where) if where else ''
        sql=' FROM documents d JOIN revisions r ON r.id=d.current_revision JOIN categories c ON c.id=r.category_id'
        with self.database() as db:
            count=db.execute('SELECT COUNT(*)'+sql+clause,args).fetchone()[0];pages=max(1,(count+19)//20);page=max(1,min(page,pages))
            rows=[dict(r) for r in db.execute('SELECT d.id,d.status,d.updated_at,r.title,r.summary,r.body,r.tags,r.number,r.filename,c.name AS category'+sql+clause+' ORDER BY d.updated_at DESC,d.id DESC LIMIT 20 OFFSET ?',args+[(page-1)*20])]
        for r in rows:
            body=r['body'];start=0
            if q:
                match=folded(body).find(folded(q).split()[0]);start=max(0,match-60)
            r['excerpt']=body[start:start+240]
        return dict(rows=rows,count=count,page=page,pages=pages)
    def summary(self):
        with self.database() as db:
            counts={r['status']:r['n'] for r in db.execute('SELECT status,COUNT(*) AS n FROM documents GROUP BY status')}
        return {k:counts.get(k,0) for k in STATES}
    def download(self,id,revision=None):
        document=self.get(id)
        if not document:raise KnowledgeError('资料不存在。')
        item=self.revision(id,revision) if revision else document
        if not item:raise KnowledgeError('资料版本不存在。')
        digest=item['digest']
        if not re.fullmatch(r'[a-f0-9]{64}',digest):raise KnowledgeError('原文件校验失败。')
        path=self.files/(digest+'.blob')
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise KnowledgeError('原文件暂不可用，请联系管理员检查备份。')
        return path,item['filename']
