"""Customer inquiry records. Independent from analytics events and delivery receipts."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
import uuid
import zipfile

STAGES = {'pending':'待核实', 'qualified':'有效询盘', 'working':'洽谈中', 'sample':'打样中',
          'won':'已成交', 'lost':'已流失', 'disqualified':'无效询盘'}
KINDS = {'customer':'真实客户', 'unknown':'待确认', 'test':'测试记录'}
EVIDENCE = {'unknown':'来源未知', 'customer':'客户自述', 'manual':'人工核对', 'tracking':'原始跟踪参数'}
FIELDS = ['external_id','received_at','record_type','name','email','company','country','phone',
          'product','requirements','form_type','source','medium','campaign','term','content',
          'gclid','gbraid','wbraid','first_landing_page','submit_page','referrer','evidence','source_note']
LIMITS = dict.fromkeys(FIELDS,200)
LIMITS.update(requirements=4000,source_note=1000,first_landing_page=500,submit_page=500,referrer=250,
              external_id=120,record_type=20,evidence=20,form_type=50,email=254)

class LeadError(ValueError):
    pass

def now():
    return datetime.now(timezone.utc).isoformat()

def clean_text(value, limit=200):
    value=str(value or '').strip()
    if len(value)>limit or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',value):
        raise LeadError('字段过长或含有不支持的控制字符。')
    return value

def day(value):
    try: return date.fromisoformat(str(value)).isoformat()
    except ValueError: raise LeadError('请填写有效日期（YYYY-MM-DD）。') from None

def own_page(value,domain):
    if not value:return ''
    try:
        p=urlsplit(value)
        if p.username or p.password or p.port or '\\' in value or re.search(r'[\s<>]',value):raise ValueError()
        if p.netloc and (p.scheme!='https' or p.hostname not in {domain,'www.'+domain}):raise ValueError()
        if not p.netloc and (p.scheme or not value.startswith('/') or value.startswith('//')):raise ValueError()
        # Query strings can contain customer details. Store paths only, never arbitrary URLs.
        path=p.path or '/'
        if not re.fullmatch(r'/[a-zA-Z0-9_./%~-]*',path):raise ValueError()
        return path
    except ValueError:raise LeadError('落地页与提交页须为本站 HTTPS 链接或页面路径；查询参数会移除。') from None

def clean_record(raw,domain):
    r={k:clean_text(raw.get(k,''),LIMITS[k]) for k in FIELDS}
    if isinstance(raw.get('received_at'),datetime):r['received_at']=raw['received_at'].date().isoformat()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,120}',r['external_id']):
        raise LeadError('外部编号须为 1–120 位字母、数字、下划线或连字符。')
    r['received_at']=day(r['received_at'])
    if r['received_at']>date.today().isoformat():raise LeadError('收到日期不能晚于今天。')
    r['record_type']=r['record_type'] or 'unknown'
    r['evidence']=r['evidence'] or 'unknown'
    if r['record_type'] not in KINDS or r['evidence'] not in EVIDENCE:raise LeadError('请选择有效的记录类型和来源依据。')
    if not r['email'] and not r['phone']:raise LeadError('至少填写邮箱或电话 / WhatsApp，才能建立客户记录。')
    if r['email'] and not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',r['email']):raise LeadError('邮箱格式不正确。')
    if not r['requirements'] and not r['product']:raise LeadError('请至少填写关注产品或客户需求。')
    for key in ['source','medium','campaign','term','content','gclid','gbraid','wbraid']:
        if '@' in r[key] or re.search(r'https?://',r[key],re.I):
            raise LeadError('来源和跟踪参数不能包含邮箱或完整网址；客户资料请填入专用字段。')
    for key in ['gclid','gbraid','wbraid']:
        if r[key] and not re.fullmatch(r'[a-zA-Z0-9_.~-]{1,200}',r[key]):raise LeadError('广告点击标识格式不正确。')
    for key in ['first_landing_page','submit_page']:r[key]=own_page(r[key],domain)
    if r['referrer']:
        try:
            p=urlsplit(r['referrer'] if '://' in r['referrer'] else 'https://'+r['referrer'])
            host=p.hostname or ''
            if p.scheme not in {'http','https'} or p.username or p.password or p.port or not re.fullmatch(r'[a-zA-Z0-9.-]+',host):raise ValueError()
            r['referrer']=host.lower()
        except ValueError:raise LeadError('来源网站请填写域名；路径和查询参数不会保存。') from None
    has_source=any(r[k] for k in ['source','medium','campaign','gclid','gbraid','wbraid','referrer'])
    if has_source and r['evidence']=='unknown':raise LeadError('填写来源后，请选择来源依据，避免把推测当成已验证归因。')
    if r['evidence']!='unknown' and not has_source:raise LeadError('请选择来源未知，或补充来源信息。')
    return r

def parse_file(data,filename,domain):
    if len(data)>5*1024*1024:raise LeadError('文件不能超过 5 MB。')
    try:
        if filename.lower().endswith('.csv'):
            text=data.decode('utf-8-sig');rows=[]
            for row in csv.reader(io.StringIO(text)):
                rows.append(row)
                if len(rows)>2001:raise LeadError('单次最多导入 2,000 条询盘。')
        elif filename.lower().endswith('.xlsx'):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if sum(x.file_size for x in z.infolist())>20*1024*1024:raise LeadError('Excel 解压内容不能超过 20 MB。')
            from openpyxl import load_workbook
            book=load_workbook(io.BytesIO(data),read_only=True,data_only=False)
            try:
                if len(book.sheetnames)!=1:raise LeadError('请使用只有一个工作表的询盘文件。')
                rows=[]
                for row in book.active.iter_rows(values_only=True):
                    rows.append(list(row))
                    if len(rows)>2001:raise LeadError('单次最多导入 2,000 条询盘。')
            finally:book.close()
        else:raise LeadError('请上传 UTF-8 CSV 或 .xlsx 文件。')
        if not rows:raise LeadError('文件为空。')
        headers=[str(x or '').strip() for x in rows[0]]
        if len(set(headers))!=len(headers) or set(headers)-set(FIELDS):raise LeadError('列名重复或不受支持，请使用询盘模板。')
        if not {'external_id','received_at'}.issubset(headers):raise LeadError('缺少 external_id 或 received_at 列。')
        if len(rows)>2001:raise LeadError('单次最多导入 2,000 条询盘。')
        result=[];seen=set()
        for number,row in enumerate(rows[1:],2):
            if not any(x is not None and str(x).strip() for x in row):continue
            if len(row)>len(headers):raise LeadError(f'第 {number} 行列数超出表头。')
            try:r=clean_record(dict(zip(headers,row)),domain)
            except LeadError as exc:raise LeadError(f'第 {number} 行：{exc}') from None
            if r['external_id'] in seen:raise LeadError(f'第 {number} 行编号重复，请先核对文件。')
            seen.add(r['external_id']);result.append(r)
        if not result:raise LeadError('文件没有可导入的询盘记录。')
        return result
    except Exception as exc:
        if isinstance(exc,LeadError):raise
        raise LeadError('无法读取文件，请使用未加密的 UTF-8 CSV 或 Excel 模板。') from None

class LeadStore:
    def __init__(self,domain='example.test'):
        self.folder=Path(os.environ.get('LEAD_DATA_DIR','data/customer-inquiries'))
        self.domain=domain.lower().removeprefix('www.')

    @contextmanager
    def database(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.folder/'leads.db',timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:
                db.execute('''CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY, external_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                    original TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'pending', owner TEXT NOT NULL DEFAULT '',
                    next_contact TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1, origin TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
                db.execute('''CREATE TABLE IF NOT EXISTS lead_history (
                    id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL, at TEXT NOT NULL,
                    action TEXT NOT NULL, detail TEXT NOT NULL)''')
                db.execute('''CREATE TABLE IF NOT EXISTS lead_imports (
                    id INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE, at TEXT NOT NULL,
                    added INTEGER NOT NULL, skipped INTEGER NOT NULL)''')
                yield db
        finally:db.close()

    def _read(self,row):
        if not row:return None
        r=dict(row);r.update(json.loads(r.pop('payload')));r.pop('original',None)
        return r

    def get(self,lead_id):
        with self.database() as db:return self._read(db.execute('SELECT * FROM leads WHERE id=?',(lead_id,)).fetchone())

    def all(self):
        with self.database() as db:return [self._read(r) for r in db.execute('SELECT * FROM leads ORDER BY id DESC')]

    def history(self,lead_id):
        with self.database() as db:
            return [dict(r,detail=json.loads(r['detail'])) for r in db.execute('SELECT * FROM lead_history WHERE lead_id=? ORDER BY id DESC',(lead_id,))]

    def _event(self,db,lead_id,action,detail):
        db.execute('INSERT INTO lead_history (lead_id,at,action,detail) VALUES (?,?,?,?)',
                   (lead_id,now(),action,json.dumps(detail,ensure_ascii=False)))

    def add_many(self,records,origin='manual',digest=''):
        # Validate the complete batch before writing. Existing records are never overwritten by import.
        records=[clean_record(r,self.domain) for r in records]
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            if digest:
                old=db.execute('SELECT * FROM lead_imports WHERE digest=?',(digest,)).fetchone()
                if old:return dict(added=0,skipped=len(records),repeated=True)
            added=skipped=0;ids=[]
            for r in records:
                if db.execute('SELECT id FROM leads WHERE external_id=?',(r['external_id'],)).fetchone():
                    skipped+=1;continue
                payload=json.dumps(r,ensure_ascii=False);stamp=now()
                cur=db.execute('INSERT INTO leads(external_id,payload,original,origin,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                    (r['external_id'],payload,payload,origin,stamp,stamp))
                ids.append(cur.lastrowid);added+=1
                self._event(db,cur.lastrowid,'建立记录',dict(接入方式='人工录入' if origin=='manual' else '文件导入',初始状态=STAGES['pending']))
            if digest:db.execute('INSERT INTO lead_imports(digest,at,added,skipped) VALUES(?,?,?,?)',(digest,now(),added,skipped))
            return dict(added=added,skipped=skipped,repeated=False,ids=ids)

    def update(self,lead_id,version,*,stage,owner,next_contact,record_type,note,archived):
        if stage not in STAGES or record_type not in KINDS or archived not in {'0','1'}:raise LeadError('请选择有效的状态和记录类型。')
        owner=clean_text(owner,80);note=clean_text(note,4000)
        next_contact=day(next_contact) if next_contact else ''
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            old=self._read(db.execute('SELECT * FROM leads WHERE id=?',(lead_id,)).fetchone())
            if not old:raise LeadError('询盘记录不存在。')
            if old['version']!=version:raise LeadError('记录已被更新，请刷新后重新核对，避免覆盖他人的跟进。')
            changes={}
            for key,value in dict(stage=stage,owner=owner,next_contact=next_contact,record_type=record_type,archived=int(archived)).items():
                if old[key]!=value:changes[key]={'before':old[key],'after':value}
            if not changes and not note:raise LeadError('请填写跟进备注或更新一项状态。')
            if (('stage' in changes and stage in {'lost','disqualified','won'}) or 'record_type' in changes or 'archived' in changes) and not note:
                raise LeadError('成交、流失、无效、记录类型或归档变更需要填写原因 / 依据。')
            if stage in {'qualified','working','sample'} and (not owner or not next_contact):
                raise LeadError('有效询盘、洽谈和打样阶段，请同时安排负责人及下次跟进日期。')
            payload={k:old[k] for k in FIELDS};payload['record_type']=record_type
            db.execute('UPDATE leads SET payload=?,stage=?,owner=?,next_contact=?,archived=?,version=version+1,updated_at=? WHERE id=?',
                (json.dumps(payload,ensure_ascii=False),stage,owner,next_contact,int(archived),now(),lead_id))
            self._event(db,lead_id,'跟进更新',dict(changes=changes,note=note))

    def summary(self,today=None):
        today=today or date.today().isoformat();rows=self.all()
        real=[r for r in rows if not r['archived'] and r['record_type']=='customer']
        active=[r for r in real if r['stage'] not in {'won','lost','disqualified'}]
        return dict(total=len(real),pending=sum(r['stage']=='pending' for r in real),
            overdue=sum(bool(r['next_contact']) and r['next_contact']<today for r in active),
            due=sum(bool(r['next_contact']) and r['next_contact']<=today for r in active),
            unattributed=sum(r['evidence']=='unknown' for r in real),
            unknown=sum(not r['archived'] and r['record_type']=='unknown' for r in rows),
            test=sum(not r['archived'] and r['record_type']=='test' for r in rows),
            stages={k:sum(r['stage']==k for r in real) for k in STAGES})

    def correct(self,lead_id,version,raw,note):
        note=clean_text(note,4000)
        if not note:raise LeadError('请填写资料更正的原因或来源依据。')
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            old=self._read(db.execute('SELECT * FROM leads WHERE id=?',(lead_id,)).fetchone())
            if not old:raise LeadError('询盘记录不存在。')
            if old['version']!=version:raise LeadError('记录已被更新，请刷新后重新核对。')
            raw={**raw,'external_id':old['external_id'],'record_type':old['record_type']}
            current=clean_record(raw,self.domain)
            changes={k:dict(before=old[k],after=current[k]) for k in FIELDS if old[k]!=current[k]}
            if not changes:raise LeadError('资料没有变化。')
            db.execute('UPDATE leads SET payload=?,version=version+1,updated_at=? WHERE id=?',
                       (json.dumps(current,ensure_ascii=False),now(),lead_id))
            self._event(db,lead_id,'资料更正',dict(changes=changes,note=note))

def select_rows(store,*,q='',stage='',kind='customer',owner='',due='',archive='active',today=''):
    if stage and stage not in STAGES or kind not in {'all',*KINDS} or due not in {'','due','overdue','unassigned','unknown'} or archive not in {'active','archived','all'}:
        raise LeadError('筛选条件无效。')
    today=today or date.today().isoformat();q=clean_text(q,200).casefold();owner=clean_text(owner,80)
    result=[]
    for r in store.all():
        if archive!='all' and bool(r['archived'])!=(archive=='archived'):continue
        if stage and r['stage']!=stage or kind!='all' and r['record_type']!=kind:continue
        if owner and r['owner']!=owner:continue
        if q and q not in ' '.join(str(r[k]) for k in ['external_id','name','email','company','phone','product','requirements','source','campaign']).casefold():continue
        if due in {'due','overdue'} and (r['stage'] in {'won','lost','disqualified'} or not r['next_contact'] or r['next_contact']>today or due=='overdue' and r['next_contact']==today):continue
        if due=='unassigned' and r['owner']:continue
        if due=='unknown' and r['evidence']!='unknown':continue
        result.append(r)
    return result

def csv_safe(value):
    text=str(value or '')
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
