"""Google CSV/XLSX snapshots. Never updates monitoring databases or OAuth files."""
from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import zipfile

from openpyxl import load_workbook


class ImportErrorDetail(ValueError):
    pass


def norm(value):
    return re.sub(r'[\s_（）()/：:%-]+', '', str(value or '')).casefold()


ALIASES = {
    'date': ['日期', 'Date'],
    'active': ['活跃用户', '活跃用户数', 'Active users', 'activeUsers'],
    'sessions': ['会话数', 'Sessions'],
    'views': ['浏览次数', '浏览量', 'Views', 'screenPageViews'],
    'events': ['关键事件数', '关键事件', 'Key events', 'keyEvents'],
    'clicks': ['点击次数', 'Clicks'],
    'impressions': ['展示', '展示次数', '曝光次数', 'Impressions'],
    'ctr': ['点击率', 'CTR'],
    'position': ['排名', '平均排名', 'Position', 'Average position'],
    'source': ['带来会话的主渠道组（默认渠道组）', 'Session primary channel group (Default channel group)',
               'Session default channel group', '带来会话的默认渠道组', '带来会话的来源/媒介', 'Session source / medium',
               'Session source/medium', '流量来源', '来源/媒介', '来源'],
    'page': ['排名靠前的网页', 'Top pages', 'Page', '网页', '网页路径和屏幕类', 'Page path and screen class',
             '网页标题和屏幕类', 'Page title and screen class', '网页路径', 'Page path', '着陆页', 'Landing page'],
    'country': ['国家/地区', 'Country', 'Country/Region'],
    'query': ['热门查询', 'Top queries', 'Query', '查询', '查询数'],
    'device': ['设备', 'Device'],
}
LOOKUP = {norm(alias):key for key, aliases in ALIASES.items() for alias in aliases}


def number(value, ratio=False):
    if value is None or str(value).strip() in ('', '—', '-', '(not set)'):
        return None
    text = str(value).strip().replace(',', '')
    percent = text.endswith('%')
    try:
        result = float(text.rstrip('%'))
    except (ValueError, TypeError):
        raise ImportErrorDetail('数值格式不正确，请使用 Google 原始导出文件。') from None
    if not math.isfinite(result) or result < 0:
        raise ImportErrorDetail('数据包含无效或负数指标。')
    if percent:
        result /= 100
    if ratio and result > 1:
        raise ImportErrorDetail('点击率必须为 0–1 的小数或百分数。')
    return result


def day(value):
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%Y%m%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ImportErrorDetail('日期格式无法识别，请使用 YYYY-MM-DD 或 YYYYMMDD。')


def read_tables(filename, content):
    suffix = Path(filename).suffix.lower()
    if suffix == '.csv':
        text = None
        encodings=['utf-16'] if content.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
        for encoding in encodings:
            try:
                text = content.decode(encoding)
                break
            except UnicodeError:
                pass
        if text is None or '\x00' in text:
            raise ImportErrorDetail('CSV 编码无法识别，请使用 UTF-8 文件。')
        rows = list(csv.reader(io.StringIO(text)))
        return [('CSV', rows)]
    if suffix != '.xlsx':
        raise ImportErrorDetail('只支持 .csv 和 .xlsx 文件，不支持旧版 .xls。')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            if sum(i.file_size for i in z.infolist()) > 50*1024*1024:
                raise ImportErrorDetail('Excel 展开后过大，请缩小报表日期范围。')
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            if len(wb.sheetnames) > 20:
                raise ImportErrorDetail('Excel 工作表过多。')
            tables = []
            cells = 0
            for sheet in wb:
                rows = []
                for row in sheet.iter_rows(values_only=True):
                    cells += len(row)
                    if cells > 500000:
                        raise ImportErrorDetail('Excel 单元格过多，请缩小报表。')
                    rows.append(list(row))
                tables.append((sheet.title, rows))
            return tables
        finally:
            wb.close()
    except ImportErrorDetail:
        raise
    except Exception:
        raise ImportErrorDetail('文件不是可读取的 Excel 工作簿。') from None


def parse_export(kind, filename, content, start='', end=''):
    if kind not in ('analytics', 'search'):
        raise ImportErrorDetail('请选择 GA4 或 Search Console。')
    if len(content) > 10*1024*1024:
        raise ImportErrorDetail('每个文件最大 10MB。')
    tables = read_tables(filename, content)
    # Official GA4 CSV comments carry exact date metadata; don't guess from download time.
    dates = {}
    for _, rows in tables:
        for row in rows[:40]:
            line = str(row[0] or '') if row else ''
            for key, pattern in [('start', r'(?:开始日期|Start date)\s*[:：]\s*(\d{8}|\d{4}-\d{2}-\d{2})'),
                                 ('end', r'(?:结束日期|End date)\s*[:：]\s*(\d{8}|\d{4}-\d{2}-\d{2})')]:
                match = re.search(pattern, line, re.I)
                if match:
                    dates[key] = day(match.group(1))
    if dates.get('start') and start and day(start) != dates['start']:
        raise ImportErrorDetail('所填开始日期与 CSV 导出日期不同，请按文件填写。')
    if dates.get('end') and end and day(end) != dates['end']:
        raise ImportErrorDetail('所填结束日期与 CSV 导出日期不同，请按文件填写。')
    start = dates.get('start') or (day(start) if start else '')
    end = dates.get('end') or (day(end) if end else '')
    sections = {}
    daily = []
    total = [None]*4
    recognized = 0
    metric_order = ['clicks', 'impressions', 'ctr', 'position'] if kind == 'search' else ['active','sessions','views','events']
    for _, rows in tables:
        if len(rows) > 50001:
            raise ImportErrorDetail('数据行超过 50000，请缩小报表。')
        header_index = None
        for i, row in enumerate(rows[:60]):
            columns = {LOOKUP[norm(value)]:j for j,value in enumerate(row) if norm(value) in LOOKUP}
            if ('clicks' in columns and 'impressions' in columns) if kind == 'search' else any(k in columns for k in ('active','sessions','views','events')):
                header_index = i
                break
        if header_index is None:
            continue
        dimension = next((d for d in ['date','query','source','page','country','device'] if d in columns), None)
        if kind == 'analytics' and 'clicks' in columns and 'impressions' in columns:
            continue
        if dimension is None:
            continue
        recognized += 1
        parsed = []
        for row in rows[header_index+1:]:
            if not row or all(v in (None,'') for v in row):
                continue
            if str(row[0] or '').startswith('#'):
                continue
            def value(key):
                j = columns.get(key)
                return row[j] if j is not None and j < len(row) else None
            label = str(value(dimension) or '').strip()
            if not label:
                continue
            if norm(label) == norm(rows[header_index][columns[dimension]]):
                raise ImportErrorDetail('文件包含多个对比区段，请关闭日期/人群比较后重新导出。')
            values = [number(value(k), ratio=k=='ctr') for k in metric_order]
            if kind == 'search' and any(v is None for v in values[:2]):
                raise ImportErrorDetail('点击或曝光数据缺失，请使用完整的原始导出。')
            if dimension == 'date':
                label = day(value('date'))
            parsed.append({'label':label[:2000], 'values':values})
        if dimension == 'date':
            daily.extend(parsed)
        else:
            sections[dimension] = parsed
    if not recognized:
        raise ImportErrorDetail('未识别到报表列。GA4 请导出流量获取/网页和屏幕报告；Search Console 请导出效果报告。')
    daily.sort(key=lambda r:r['label'])
    if daily and len({r['label'] for r in daily}) != len(daily):
        raise ImportErrorDetail('每日数据中有重复日期，请关闭比较或合并重复行。')
    if not start or not end:
        raise ImportErrorDetail('该文件未包含完整报表日期，请填写原始导出时选择的开始、结束日期。')
    if start > end:
        raise ImportErrorDetail('开始日期不能晚于结束日期。')
    if any(not start <= r['label'] <= end for r in daily):
        raise ImportErrorDetail('每日数据超出填写的报表范围，请核对日期。')
    if kind == 'search' and daily:
        clicks = sum((r['values'][0] or 0) for r in daily)
        impressions = sum((r['values'][1] or 0) for r in daily)
        weighted = sum((r['values'][3] or 0)*(r['values'][1] or 0) for r in daily)
        total = [clicks, impressions, clicks/impressions if impressions else None,
                 weighted/impressions if impressions and all(r['values'][3] is not None for r in daily if r['values'][1]) else None]
    return {'start':start,'end':end,'daily':daily,'sections':sections,'total':total,
            'available_start': daily[0]['label'] if daily else None,
            'available_end':daily[-1]['label'] if daily else None,
            'row_count':len(daily)+sum(len(r) for r in sections.values())}


class ImportStore:
    def __init__(self):
        self.folder = Path(os.environ.get('GOOGLE_IMPORT_DIR', 'data/google-imports'))

    def connect(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.folder/'imports.db', timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS imports (id INTEGER PRIMARY KEY, kind TEXT, filename TEXT, digest TEXT, resource TEXT, imported_at TEXT, start TEXT, end TEXT, payload TEXT, UNIQUE(kind,digest,resource,start,end))')
        return db

    def history(self, kind=None):
        with self.database() as db:
            sql = 'SELECT id,kind,filename,resource,imported_at,start,end FROM imports'
            args = ()
            if kind:
                sql += ' WHERE kind=?'; args=(kind,)
            return [dict(r) for r in db.execute(sql+' ORDER BY id DESC LIMIT 100',args)]

    def save(self, kind, filename, content, resource, start='', end=''):
        if not resource.strip():
            raise ImportErrorDetail('请先在连接设置填写对应的 GA4 资源 ID 或网站资源。')
        payload = parse_export(kind, filename, content, start, end)
        digest = hashlib.sha256(content).hexdigest()
        suffix = Path(filename).suffix.lower()
        clean = Path(filename.replace('\\','/')).stem[:150]+suffix
        with self.database() as db:
            prior = db.execute('SELECT id FROM imports WHERE kind=? AND digest=? AND resource=? AND start=? AND end=?',
                               (kind,digest,resource,payload['start'],payload['end'])).fetchone()
            if prior:
                return prior['id'], False
            raw = self.folder/(digest+suffix)
            if not raw.exists():
                raw.write_bytes(content)
            cur = db.execute('INSERT INTO imports(kind,filename,digest,resource,imported_at,start,end,payload) VALUES(?,?,?,?,?,?,?,?)',
                             (kind,clean,digest,resource,datetime.now(timezone.utc).isoformat(),payload['start'],payload['end'],json.dumps(payload,ensure_ascii=False)))
            return cur.lastrowid, True

    def get(self, kind, batch=None, resource=''):
        with self.database() as db:
            args=[kind]
            where='kind=?'
            if resource:
                where+=' AND resource=?';args.append(resource)
            if batch:
                where+=' AND id=?';args.append(batch)
            row=db.execute('SELECT * FROM imports WHERE '+where+' ORDER BY id DESC LIMIT 1',args).fetchone()
            if not row:
                return None
            selected=dict(row)
            same=db.execute('SELECT * FROM imports WHERE kind=? AND resource=? AND start=? AND end=? AND id<=? ORDER BY id',
                            (kind,selected['resource'],selected['start'],selected['end'],selected['id'])).fetchall()
        combined={'total':[None]*4, 'daily':[], 'sections':{}, 'section_meta':{}}
        for item in same:
            payload=json.loads(item['payload'])
            combined['sections'].update(payload['sections'])
            for dim in payload['sections']:
                combined['section_meta'][dim]={k:item[k] for k in ['id','filename','resource','imported_at','start','end']}
            if payload['daily']:
                combined['daily']=payload['daily']
                combined['total']=payload['total']
        sections=combined.pop('sections')
        if kind=='analytics':
            combined['source_rows'] = sections.get('source', [])
            # Users are non-additive. Do not present grouped or daily user sums as unique users.
            for metric,dim in [(1,'source'),(2,'page'),(3,'source')]:
                rows=combined['daily'] or sections.get(dim,[])
                if rows and all(r['values'][metric] is not None for r in rows):
                    combined['total'][metric]=sum(r['values'][metric] for r in rows)
            combined['daily']=[{'label':r['label'],'values':[r['values'][1]]} for r in combined['daily'] if r['values'][1] is not None]
            names={'source':'流量来源 / 渠道','page':'热门页面','country':'访客国家'}
            combined['tables']=[(name,[{'label':r['label'],'values':[r['values'][{'source':1,'page':2,'country':0}[dim]]]} for r in sections[dim]][:10]) for dim,name in names.items() if dim in sections]
        else:
            combined['query_rows'] = sections.get('query', [])
            combined['page_rows'] = sections.get('page', [])
            names={'query':'搜索关键词','page':'搜索落地页','country':'搜索用户国家','device':'设备'}
            combined['tables']=[(name,sections[dim][:10]) for dim,name in names.items() if dim in sections]
        combined['import_meta']={k:selected[k] for k in ['id','filename','resource','imported_at','start','end']}
        combined['daily']=sorted(combined['daily'],key=lambda r:r['label'])
        return combined

    def original(self, batch):
        with self.database() as db:
            row=db.execute('SELECT * FROM imports WHERE id=?',(batch,)).fetchone()
        if not row:
            return None
        return self.folder/(row['digest']+Path(row['filename']).suffix.lower()), row['filename']

    @contextmanager
    def database(self):
        db=self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()
