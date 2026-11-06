"""Inquiry receipts and GA4 event counts have deliberately separate meanings."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import re
import csv
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .google_imports import read_tables, norm, day, ImportErrorDetail
from .operations_store import OperationsError

EVENTS = {'inquiry_start':'开始填写', 'inquiry_submit_attempt':'尝试提交', 'generate_lead':'GA4 成功事件',
          'inquiry_error':'表单错误', 'contact_click':'联系方式点击'}
KINDS = {'customer':'客户','test':'测试','unknown':'未标识'}
OUTCOMES = {'accepted':'已接收','rejected':'被拒绝','error':'提交错误','pending':'待确认'}
ALIASES = {
 'request_id':['request_id','请求编号','询盘编号'], 'occurred_at':['occurred_at','发生时间','时间'],
 'outcome':['outcome','接收结果','结果'], 'http_status':['http_status','HTTP状态'],
 'is_test':['is_test','是否测试','记录类型'], 'form_id':['form_id','表单类型'],
 'page_path':['page_path','页面路径','Page path'], 'error_code':['error_code','错误类型'],
 'event':['event_name','eventName','事件名称','Event name'], 'count':['event_count','eventCount','事件数','事件计数','Event count'],
 'date':['date','日期'],
}
LOOKUP = {norm(a):k for k,aliases in ALIASES.items() for a in aliases}


def dates(start, end, tz='Asia/Shanghai', maximum=366):
    try:
        begin, finish = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError,TypeError):
        raise OperationsError('请选择有效的开始和结束日期。') from None
    if begin>finish or (finish-begin).days>=maximum or finish>datetime.now(ZoneInfo(tz)).date():
        raise OperationsError(f'日期范围须按先后顺序填写，最多 {maximum} 天，且不能晚于今天。')
    return begin.isoformat(), finish.isoformat()


def record_kind(value):
    value = str('' if value is None else value).strip().casefold()
    if value in {'1','true','yes','是','test','测试'}: return 'test'
    if value in {'0','false','no','否','customer','客户'}: return 'customer'
    if value in {'','unknown','未标识','未知','—','(not set)'}: return 'unknown'
    raise OperationsError('是否测试只接受 true/false、测试/客户；未填写会列为未标识，不会当作客户。')


def clean_path(value, domain):
    value = str(value or '').strip()
    if not value: return ''
    p = urlsplit(value)
    if p.scheme or p.netloc:
        if p.scheme!='https' or p.hostname not in {domain,'www.'+domain} or p.username or p.password or p.port:
            raise OperationsError('页面路径必须属于当前网站。')
    path = p.path
    if not re.fullmatch(r'/[a-zA-Z0-9/_-]*', path) or len(path)>250:
        raise OperationsError('页面路径请只保留网址路径，不包含联系信息或查询参数。')
    return path


def parse_records(kind, filename, content, start, end, domain, tz):
    start,end = dates(start,end,tz)
    if len(content)>5*1024*1024:
        raise OperationsError('文件最大 5 MB。')
    try:
        tables = read_tables(filename,content)
    except (ImportErrorDetail,csv.Error) as exc:
        raise OperationsError(str(exc)) from None
    required = {'request_id','occurred_at','outcome'} if kind=='receipts' else {'event','count'}
    rows, ignored = [], 0
    for _, table in tables:
        header = None
        for line in table:
            columns = {LOOKUP[norm(cell)]:i for i,cell in enumerate(line) if norm(cell) in LOOKUP}
            if required.issubset(columns):
                header = columns
                continue
            if header is None or not any(str(x or '').strip() for x in line) or str(line[0] or '').startswith('#'):
                continue
            def field(key):
                pos = header.get(key)
                return line[pos] if pos is not None and pos<len(line) else ''
            if len(rows)>=5000:
                raise OperationsError('每次最多导入 5000 行，请缩小导出范围。')
            if kind=='ga4':
                event = str(field('event') or '').strip()
                if event not in EVENTS:
                    ignored += 1; continue
                try:
                    count = float(str(field('count')).replace(',',''))
                    if not count.is_integer() or count<0 or count>10**12: raise ValueError
                    stamp = day(field('date')) if field('date') else ''
                except (ValueError,ImportErrorDetail):
                    raise OperationsError('事件数须为非负整数；日期使用 YYYY-MM-DD 或 YYYYMMDD。') from None
                if stamp and not start<=stamp<=end:
                    raise OperationsError('事件日期超出所填报表范围，请按官方导出日期填写。')
                rows.append(dict(event=event,count=int(count),date=stamp,kind=record_kind(field('is_test')),
                                 page=clean_path(field('page_path'),domain)))
            else:
                rid = str(field('request_id') or '').strip()
                if not re.fullmatch(r'[A-Za-z0-9._:-]{1,120}',rid):
                    raise OperationsError('请求编号须为系统生成的编号，不能包含姓名、邮箱、空格或表单正文。')
                try:
                    stamp = field('occurred_at')
                    parsed = stamp if isinstance(stamp,datetime) else datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
                    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=ZoneInfo(tz))
                except (ValueError,TypeError):
                    raise OperationsError('发生时间无法识别，请使用带时区的 ISO 时间，例如 2026-10-24T10:00:00+08:00。') from None
                if not start<=parsed.astimezone(ZoneInfo(tz)).date().isoformat()<=end:
                    raise OperationsError('接收记录时间超出所填报表范围。')
                outcome = str(field('outcome')).strip().casefold()
                outcome = {'已接收':'accepted','被拒绝':'rejected','提交错误':'error','待确认':'pending'}.get(outcome,outcome)
                if outcome not in OUTCOMES: raise OperationsError('接收结果只接受 accepted/rejected/error/pending。HTTP 200 本身不能证明接收成功。')
                status = str('' if field('http_status') is None else field('http_status')).strip()
                if status and (not status.isdigit() or not 100<=int(status)<=599): raise OperationsError('HTTP 状态须为 100–599 的整数或留空。')
                if outcome=='accepted' and status and not 200<=int(status)<300:
                    raise OperationsError('已接收记录与失败 HTTP 状态冲突，请核对后导入。')
                error = str(field('error_code') or '').strip()
                if error not in {'','abuse_check_failed','rate_limited','validation_error','server_error','network_error','receipt_unavailable'}: error='other'
                form = str(field('form_id') or '').strip()
                if form not in {'rfq','sample_request','product_brief'}: form='unknown'
                rows.append(dict(request_id=rid,occurred_at=parsed.astimezone(timezone.utc).isoformat(),
                    outcome=outcome,http_status=int(status) if status else None,kind=record_kind(field('is_test')),
                    form=form,page=clean_path(field('page_path'),domain),error_code=error))
    if not rows:
        raise OperationsError('未找到有效接收记录。请使用下载模板的表头。' if kind=='receipts' else '未找到询盘相关事件。请导出事件名称与事件数，不要上传关键事件总计。')
    if kind=='ga4' and len({(r['event'],r['date'],r['kind'],r['page']) for r in rows})!=len(rows):
        raise OperationsError('事件报表含重复维度，请导出事件名称 / 日期的汇总，不要混合未提供的其他分组维度。')
    if kind=='receipts' and len({r['request_id'] for r in rows})!=len(rows):
        raise OperationsError('同一文件含重复请求编号，请保留每个编号的最终接收结果。')
    return dict(rows=rows,ignored=ignored,source='file',digest=hashlib.sha256(content).hexdigest())


def inquiry_summary(store, resource, start, end, tz, record_type='customer', batch_id=None, status=''):
    receipt_batches = store.batches('receipts','https://'+store.domain+'/')
    unique = {}
    # Newer occurrence wins; reimporting another file never counts a request twice.
    for batch in reversed(receipt_batches):
        for row in batch['payload']['rows']:
            old=unique.get(row['request_id'])
            if old is None or row['occurred_at']>=old['occurred_at']:
                unique[row['request_id']]={**row,'batch':batch['id'],'filename':batch['filename']}
    current = [r for r in unique.values() if start<=datetime.fromisoformat(r['occurred_at']).astimezone(ZoneInfo(tz)).date().isoformat()<=end]
    customers = [r for r in current if r['kind']=='customer']
    visible = [r for r in current if (record_type=='all' or r['kind']==record_type) and (not status or r['outcome']==status)]
    visible.sort(key=lambda r:r['occurred_at'],reverse=True)
    ga_batches=store.batches('ga4',resource) if resource else []
    selected=next((b for b in ga_batches if b['id']==batch_id),None) if batch_id else (ga_batches[0] if ga_batches else None)
    if batch_id and selected is None: raise OperationsError('当前 GA4 资源下找不到这个事件批次。')
    ga_rows=[]; compatible=not selected or not (selected['end']<start or selected['start']>end)
    if selected:
        for row in selected['payload']['rows']:
            if row['date']:
                if start<=row['date']<=end: ga_rows.append(row)
            elif start<=selected['start'] and selected['end']<=end: ga_rows.append(row)
            else: compatible=False
    totals={key:sum(r['count'] for r in ga_rows if r['event']==key) if selected and compatible else None for key in EVENTS}
    return dict(start=start,end=end,records=visible,record_count=len(visible),receipt_batches=receipt_batches,
        available=bool(receipt_batches),accepted=sum(r['outcome']=='accepted' for r in customers) if receipt_batches else None,
        failed=sum(r['outcome'] in {'rejected','error'} for r in customers) if receipt_batches else None,
        unclassified=sum(r['kind']=='unknown' for r in current),test_count=sum(r['kind']=='test' for r in current),
        failure_403=sum(r['http_status']==403 for r in customers),ga_batches=ga_batches,ga_selected=selected,
        ga_totals=totals,ga_rows=ga_rows,compatible=compatible,
        ga_test_status='包含测试或未标识流量' if any(r['kind']!='customer' for r in ga_rows) else '文件中均标为客户事件（仍需核对）')
