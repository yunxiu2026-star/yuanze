"""Append-only local operational evidence, separate from all existing databases."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit


def now():
    return datetime.now(timezone.utc).isoformat()


class OperationsError(ValueError):
    pass


class OperationsStore:
    def __init__(self, domain, database_url):
        self.domain = domain
        parent = Path(database_url[10:]).parent if database_url.startswith('sqlite:///') else Path('data')
        self.folder = Path(os.environ.get('SITE_OPERATIONS_DIR', str(parent / 'site-operations')))
        self.baseline = json.loads((Path(__file__).parent/'data/operations-baseline.json').read_text(encoding='utf-8'))
        with self.database() as db:
            if domain == self.baseline['domain']:
                for row in self.baseline['pages']:
                    path = urlsplit(row['网址']).path
                    db.execute('INSERT OR IGNORE INTO pages(domain,path,title,policy) VALUES(?,?,?,?)',
                               (domain, path, row['页面'], 'noindex' if path in {'/get-a-quote/', '/request-samples/'} else 'index'))
                    page = db.execute('SELECT id FROM pages WHERE domain=? AND path=?', (domain,path)).fetchone()
                    key = f"baseline:{domain}:{path}"
                    state = 'indexed' if row['Google检查结果'] == '已收录' else 'unknown' if '尚未识别' in row['Google检查结果'] else 'not_indexed'
                    payload = dict(state=state, coverage=row['Google检查结果'], resource=self.baseline['resource'],
                                   note=row['建议'], historical=True)
                    db.execute('INSERT OR IGNORE INTO checks(page_id,kind,source,checked_at,payload,evidence_key) VALUES(?,?,?,?,?,?)',
                               (page['id'],'google','historical_ui',self.baseline['checked_date']+'T00:00:00+00:00',json.dumps(payload,ensure_ascii=False),key))

        if domain:
            defaults={
                '/':'Home','/products/':'Products','/ready-stock/':'Ready stock','/packaging/':'Packaging','/resources/':'Buyer guides',
                '/resources/moq-cost-structure/':'MOQ & cost structure',
                '/resources/us-eu-regulatory-readiness/':'US & EU regulatory readiness',
                '/resources/halal-bpom-market-entry/':'HALAL & BPOM market entry',
                '/resources/packaging-formula-compatibility/':'Packaging & formula compatibility'}
            with self.database() as db:
                for path,title in defaults.items():
                    for prefix,label in [('',title),('/ar','AR · '+title)]:
                        db.execute('INSERT OR IGNORE INTO pages(domain,path,title,policy) VALUES(?,?,?,?)',
                                   (domain,prefix+path,label,'index'))

    def add_sitemap_paths(self,paths):
        # Validate the whole batch first; one transaction; existing rows untouched.
        from .sitemap_sync import valid_page_path
        paths=sorted(set(paths))
        if len(paths)>5000 or any(not valid_page_path(p) for p in paths):
            raise OperationsError('站点地图路径无效或超过 5,000 页。')
        with self.database() as db:
            added=0
            for path in paths:
                title=('Home' if path=='/' else path.strip('/').replace('/',' · ').replace('-',' '))[:100]
                policy='noindex' if path in {'/get-a-quote/','/request-samples/','/ar/get-a-quote/','/ar/request-samples/'} else 'index'
                added+=db.execute('INSERT OR IGNORE INTO pages(domain,path,title,policy) VALUES(?,?,?,?)',
                                  (self.domain,path,title,policy)).rowcount
        return {'discovered':len(paths),'added':added,'existing':len(paths)-added}

    @contextmanager
    def database(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.folder/'operations.db', timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pages(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,path TEXT NOT NULL,title TEXT NOT NULL,policy TEXT NOT NULL,UNIQUE(domain,path));
                CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY,page_id INTEGER NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,checked_at TEXT NOT NULL,payload TEXT NOT NULL,evidence_key TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,kind TEXT NOT NULL,resource TEXT NOT NULL,filename TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL,imported_at TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(domain,kind,resource,start,end,digest));
            ''')
            with db:
                yield db
        finally:
            db.close()

    def pages(self):
        with self.database() as db:
            return [dict(r) for r in db.execute('SELECT * FROM pages WHERE domain=? ORDER BY id', (self.domain,))]

    def page(self, page_id):
        return next((p for p in self.pages() if p['id'] == page_id), None)

    def add_page(self, path, title, policy):
        if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path or '\\' in path or len(path)>300:
            raise OperationsError('请填写本站路径，例如 /packaging/，不要填写域名、参数或锚点。')
        import re
        if not re.fullmatch(r'/[a-zA-Z0-9/_-]*', path) or policy not in {'index','noindex'} or not title.strip() or len(title)>100:
            raise OperationsError('请填写有效的页面名称、英文网址路径与排名策略。')
        with self.database() as db:
            db.execute('INSERT OR IGNORE INTO pages(domain,path,title,policy) VALUES(?,?,?,?)', (self.domain,path,title.strip(),policy))

    def check(self, page_id, kind, source, payload, checked_at=None):
        if not self.page(page_id):
            raise OperationsError('找不到本站的这个页面。')
        with self.database() as db:
            return db.execute('INSERT INTO checks(page_id,kind,source,checked_at,payload) VALUES(?,?,?,?,?)',
                              (page_id,kind,source,checked_at or now(),json.dumps(payload,ensure_ascii=False))).lastrowid

    def checks(self, page_id=None):
        with self.database() as db:
            rows = db.execute('SELECT c.* FROM checks c JOIN pages p ON c.page_id=p.id WHERE p.domain=?'+
                              (' AND p.id=?' if page_id else '')+' ORDER BY c.checked_at DESC,c.id DESC',
                              (self.domain,page_id) if page_id else (self.domain,)).fetchall()
        return [dict(r, payload=json.loads(r['payload'])) for r in rows]

    def save_batch(self, kind, resource, filename, start, end, digest, payload):
        filename = Path(filename.replace('\\','/')).name[:180]
        with self.database() as db:
            old = db.execute('SELECT id FROM batches WHERE domain=? AND kind=? AND resource=? AND start=? AND end=? AND digest=?',
                             (self.domain,kind,resource,start,end,digest)).fetchone()
            if old:
                return old['id'], False
            batch = db.execute('INSERT INTO batches(domain,kind,resource,filename,start,end,imported_at,digest,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                (self.domain,kind,resource,filename,start,end,now(),digest,json.dumps(payload,ensure_ascii=False))).lastrowid
        return batch, True

    def batches(self, kind, resource):
        with self.database() as db:
            rows = db.execute('SELECT * FROM batches WHERE domain=? AND kind=? AND resource=? ORDER BY id DESC',
                              (self.domain,kind,resource)).fetchall()
        return [dict(r,payload=json.loads(r['payload'])) for r in rows]
