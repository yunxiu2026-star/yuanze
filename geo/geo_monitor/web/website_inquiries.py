"""Server-internal, retryable replication of accepted website inquiries.

There is no public intake endpoint here. The host bridge reads the existing website
database in a read-only transaction and passes bounded batches over Docker stdin.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sys
import uuid
import logging

from .lead_notifications import LeadNotifications
from zoneinfo import ZoneInfo

from .leads import FIELDS, LIMITS, LeadError, LeadStore, clean_record, now, own_page

TYPES={'PRODUCT_BRIEF':'product_brief','SAMPLE_REQUEST':'sample_request','RFQ':'rfq'}
ERRORS={'INVALID_RECORD':'原始接收记录格式异常','CONSENT_MISSING':'缺少询盘处理同意记录',
        'IDENTIFIER_CONFLICT':'编号与后台已有记录冲突','SOURCE_CHANGED':'同一接收编号的原始内容发生变化',
        'SOURCE_UNAVAILABLE':'暂时无法读取官网接收库','SYNC_INTERRUPTED':'上次同步中断，已重新尝试',
        'SYNC_FAILED':'同步暂未完成，将自动重试'}

def source_payload(raw):
    try:
        sid=str(uuid.UUID(str(raw['record_id'])))
        kind=str(raw['record_type'])
        stamp=datetime.fromisoformat(str(raw['created_at']).replace('Z','+00:00'))
        if kind not in TYPES or stamp.tzinfo is None:raise ValueError()
        fields=raw['submitted_fields'];context=raw['source_context']
        if not isinstance(fields,dict) or not isinstance(context,dict):raise ValueError()
        result=dict(record_id=sid,record_type=kind,created_at=stamp.isoformat(),
                    source_route=str(raw['source_route']),source_context=context,submitted_fields=fields)
        if len(json.dumps(result).encode())>100_000:raise ValueError()
        return result
    except (KeyError,ValueError,TypeError,AttributeError):raise LeadError('INVALID_RECORD') from None

def normalize(raw,domain):
    r=source_payload(raw);f=r['submitted_fields'];ctx=r['source_context']
    if f.get('consent_inquiry',f.get('consent')) is not True:raise LeadError('CONSENT_MISSING')
    def text(value,limit=200):
        value=str(value or '').strip()
        return ''.join(c for c in value if ord(c)>=32 or c in '\n\t')[:limit]
    def get(*keys):return next((f[k] for k in keys if f.get(k)), '')
    name=text(get('name','contact_name'))
    # The intake API collapses whitespace before storing popup messages.
    popup=re.match(r'\ACompany: ([^\r\n]{0,160}?)\s+WhatsApp: ([^\r\n]{0,40}?)\s+Sample request:\s*',str(f.get('message','')))
    company=text(get('company') or (popup[1] if popup else ''))
    phone=text(get('whatsapp','phone','telephone') or (popup[2] if popup and popup[2]!='Not provided' else ''))
    # Only the explicit, pre-existing synthetic fixture is classified as a test.
    test=(ctx.get('is_test') is True or (company=='B2B PRODUCTION SYNTHETIC' and get('work_email','email')=='synthetic@example.invalid'
          and name.startswith('PRODUCTION SYNTHETIC ')))
    stamp=datetime.fromisoformat(r['created_at']).astimezone(ZoneInfo('Asia/Shanghai'))
    result=dict.fromkeys(FIELDS,'')
    result.update(external_id='web-'+r['record_id'],received_at=stamp.date().isoformat(),
       record_type='test' if test else 'customer',name=name,email=text(get('work_email','email'),254),
       company=company,country=text(get('country')),phone=phone,
       product=text(get('product','product_idea','product_type','series')) or TYPES[r['record_type']],
       form_type=TYPES[r['record_type']],evidence='unknown')
    exclude={'name','contact_name','work_email','email','company','country','whatsapp','phone','telephone',
             'consent_inquiry','consent_upload','attachment'}
    result['requirements']='\n'.join(f'{key}: {value}' for key,value in f.items() if key not in exclude and value not in ('',None))[:4000]
    for key,value in [('submit_page',r['source_route']),('first_landing_page',ctx.get('first_landing_page') or ctx.get('landing_page',''))]:
        try:result[key]=own_page(text(value,500),domain)
        except LeadError:pass
    utm=ctx.get('utm',{})
    if isinstance(utm,dict):
        for key in ['source','medium','campaign','term','content']:result[key]=text(utm.get(key,''))
    for key in ['gclid','gbraid','wbraid','referrer']:result[key]=text(ctx.get(key,''))
    if any(result[k] for k in ['source','medium','campaign','gclid','gbraid','wbraid','referrer']):result['evidence']='tracking'
    result['source_note']='官网自动同步；原始接收编号 '+r['record_id']+'。跟踪参数为表单提交时记录，不等于已核实的广告归因。'
    try:return clean_record(result,domain),r
    except LeadError:
        # Bad tracking parameters must never cause a valid customer inquiry to disappear.
        for key in ['source','medium','campaign','term','content','gclid','gbraid','wbraid','referrer']:result[key]=''
        result['evidence']='unknown'
        result['source_note']+=' 部分跟踪参数格式不适用，完整原始内容保留在接收快照。'
        try:return clean_record(result,domain),r
        except LeadError:raise LeadError('INVALID_RECORD') from None

TAG_LABELS={'category':'品类','route':'合作路线','volume':'预计数量','guide':'买家指南','ad_source':'广告标记'}

def parse_buyer_tags(text):
    result={k:[] for k in TAG_LABELS}
    def add(key,value):
        value=' '.join(str(value).split())[:300]
        if value and value not in result[key] and len(result[key])<8:result[key].append(value)
    for kind,value in re.findall(r'\[(Brief|Buyer guide|Ad Source):\s*([^\[\]]{1,1600})\]',str(text or '')[:100000],re.I):
        if kind.casefold()=='brief':
            for key,part in zip(('category','route','volume'),value.split('|',2)):add(key,part)
        else:add('guide' if kind.casefold()=='buyer guide' else 'ad_source',value)
    return result

def enrich_lead(record,raw_message=''):
    from urllib.parse import urlencode
    result=dict(record)
    tags=parse_buyer_tags(str(record.get('requirements',''))+'\n'+raw_message)
    result['buyer_tags']=tags
    result['buyer_badges']=[dict(key=k,label=TAG_LABELS[k],value=v) for k in TAG_LABELS for v in tags[k]]
    result['whatsapp_url']=''
    phone=str(record.get('phone','')).strip()
    # Never guess country codes or concatenate extensions / multiple numbers.
    if re.fullmatch(r'\+?[0-9() .\-]{7,40}',phone):
        digits=re.sub('[^0-9]','',phone)
        if digits.startswith('00'):digits=digits[2:]
        if re.fullmatch(r'[1-9][0-9]{6,14}',digits):
            name=' '.join(str(record.get('name','')).split())[:120] or 'there'
            topics=tags['category'][:1]+tags['guide'][:1]
            topic=' / '.join(topics) or ' '.join(str(record.get('product','matched samples')).split())[:180]
            message=f'Hi {name}, this is {os.getenv('SITE_NAME','Demo Works')}. We received your request regarding {topic}. We can prepare matched sample options for your project. Could you confirm your target market and preferred quantity?'
            result['whatsapp_url']='https://wa.me/'+digits+'?'+urlencode({'text':message})
    return result

def filter_buyer_tags(rows,filters):
    return [r for r in rows if all(not filters.get(k) or filters[k] in r['buyer_tags'][k] for k in TAG_LABELS)]

class WebsiteSync:
    def __init__(self,store=None,notifications=None):
        self.store=store or LeadStore()
        self.notifications=notifications if notifications is not None else LeadNotifications(self.store)

    def schema(self,db):
        db.execute('''CREATE TABLE IF NOT EXISTS website_receipts (
            source_id TEXT PRIMARY KEY,lead_id INTEGER NOT NULL UNIQUE,source_created_at TEXT NOT NULL,
            payload TEXT NOT NULL,digest TEXT NOT NULL,synchronized_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS website_sync_runs (
            id INTEGER PRIMARY KEY,started_at TEXT NOT NULL,completed_at TEXT,
            status TEXT NOT NULL,read_count INTEGER NOT NULL DEFAULT 0,added INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,error_code TEXT NOT NULL DEFAULT '')''')
        db.execute('''CREATE TABLE IF NOT EXISTS website_sync_errors (
            source_id TEXT PRIMARY KEY,code TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,resolved_at TEXT)''')

        self.notifications.schema(db)

    def decorate(self,records):
        messages={}
        ids=[r['id'] for r in records]
        if ids:
            with self.store.database() as db:
                self.schema(db)
                for offset in range(0,len(ids),200):
                    part=ids[offset:offset+200]
                    for row in db.execute('SELECT lead_id,payload FROM website_receipts WHERE lead_id IN ('+','.join('?' for _ in part)+')',part):
                        try:
                            fields=json.loads(row['payload']).get('submitted_fields',{})
                            context=json.loads(row['payload']).get('source_context',{})
                            messages[row['lead_id']]='\n'.join(str(fields.get(k,'') or '') for k in ('message','requirements','request','project_context','additional_notes'))+'\n'+str(context.get('ad_source_tag',''))
                        except (ValueError,TypeError,AttributeError):pass
        return [enrich_lead(r,messages.get(r['id'],'')) for r in records]

    def start(self):
        with self.store.database() as db:
            self.schema(db)
            db.execute("UPDATE website_sync_runs SET status='interrupted',completed_at=?,error_code='SYNC_INTERRUPTED' WHERE status='running'",(now(),))
            return db.execute("INSERT INTO website_sync_runs(started_at,status) VALUES(?,'running')",(now(),)).lastrowid

    def ingest(self,records,run_id):
        if not isinstance(records,list) or len(records)>200:raise LeadError('INVALID_RECORD')
        counts=dict(read=0,added=0,skipped=0,failed=0)
        for raw in records:
            sid='invalid-'+hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()[:24]
            try:
                record,receipt=normalize(raw,self.store.domain);sid=receipt['record_id']
                payload=json.dumps(receipt,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(payload.encode()).hexdigest()
                with self.store.database() as db:
                    self.schema(db);db.execute('BEGIN IMMEDIATE')
                    run=db.execute("SELECT id FROM website_sync_runs WHERE id=? AND status='running'",(run_id,)).fetchone()
                    if not run:raise LeadError('SYNC_FAILED')
                    existing=db.execute('SELECT digest FROM website_receipts WHERE source_id=?',(sid,)).fetchone()
                    if existing:
                        if existing['digest']!=digest:raise LeadError('SOURCE_CHANGED')
                        counts['skipped']+=1
                    else:
                        if db.execute('SELECT id FROM leads WHERE external_id=?',(record['external_id'],)).fetchone():raise LeadError('IDENTIFIER_CONFLICT')
                        stamp=now();normalized=json.dumps(record,ensure_ascii=False)
                        lead_id=db.execute('INSERT INTO leads(external_id,payload,original,origin,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                            (record['external_id'],normalized,normalized,'website',stamp,stamp)).lastrowid
                        db.execute('INSERT INTO website_receipts VALUES(?,?,?,?,?,?)',(sid,lead_id,receipt['created_at'],payload,digest,stamp))
                        self.store._event(db,lead_id,'官网自动同步',dict(接入方式='官网表单',初始状态='待核实',原始接收编号=sid))
                        # The outbox is inserted alongside the accepted lead; delivery runs only after commit.
                        db.execute('SAVEPOINT notify_enqueue')
                        try:
                            raw_tags='\n'.join(str(v) for k,v in receipt['submitted_fields'].items() if k in {'message','requirements','request','project_context','additional_notes'})+'\n'+str(receipt['source_context'].get('ad_source_tag',''))
                            self.notifications.enqueue(db,lead_id,record,receipt,parse_buyer_tags(raw_tags))
                            db.execute('RELEASE notify_enqueue')
                        except Exception:
                            db.execute('ROLLBACK TO notify_enqueue');db.execute('RELEASE notify_enqueue')
                            logging.getLogger(__name__).warning('WEBSITE_NOTIFICATION_QUEUE_ERROR')
                        counts['added']+=1
                    db.execute('UPDATE website_sync_errors SET resolved_at=? WHERE source_id=?',(now(),sid))
            except LeadError as exc:
                counts['failed']+=1;code=str(exc) if str(exc) in ERRORS else 'SYNC_FAILED'
                with self.store.database() as db:
                    self.schema(db);stamp=now()
                    db.execute('INSERT INTO website_sync_errors VALUES(?,?,?,?,NULL) ON CONFLICT(source_id) DO UPDATE SET code=excluded.code,last_seen=excluded.last_seen,resolved_at=NULL',
                               (sid,code,stamp,stamp))
            counts['read']+=1
        with self.store.database() as db:
            self.schema(db)
            db.execute('UPDATE website_sync_runs SET read_count=read_count+?,added=added+?,skipped=skipped+?,failed=failed+? WHERE id=?',
                       (counts['read'],counts['added'],counts['skipped'],counts['failed'],run_id))
        return counts

    def finish(self,run_id,error=''):
        if error and error not in ERRORS:error='SYNC_FAILED'
        with self.store.database() as db:
            self.schema(db);run=db.execute('SELECT * FROM website_sync_runs WHERE id=?',(run_id,)).fetchone()
            if not run or run['status']!='running':raise LeadError('SYNC_FAILED')
            status='failed' if error or run['failed'] else 'success'
            db.execute('UPDATE website_sync_runs SET completed_at=?,status=?,error_code=? WHERE id=?',
                       (now(),status,error or ('INVALID_RECORD' if run['failed'] else ''),run_id))
        try:self.notifications.deliver_pending()
        except Exception:logging.getLogger(__name__).warning('WEBSITE_NOTIFICATION_RETRY_DEFERRED')
        return self.status()

    def snapshot(self,lead_id):
        with self.store.database() as db:
            self.schema(db);row=db.execute('SELECT * FROM website_receipts WHERE lead_id=?',(lead_id,)).fetchone()
            if not row:return None
            result=dict(row);result['payload']=json.loads(result['payload']);return result

    def status(self):
        with self.store.database() as db:
            self.schema(db)
            row=db.execute('SELECT * FROM website_sync_runs ORDER BY id DESC LIMIT 1').fetchone()
            last=db.execute("SELECT completed_at FROM website_sync_runs WHERE status='success' ORDER BY id DESC LIMIT 1").fetchone()
            total=db.execute('SELECT COUNT(*) FROM website_receipts').fetchone()[0]
            errors=[dict(r) for r in db.execute('SELECT source_id,code,last_seen FROM website_sync_errors WHERE resolved_at IS NULL ORDER BY last_seen DESC LIMIT 10')]
        result=dict(enabled=os.getenv('WEBSITE_SYNC_ENABLED')=='true',last_success=last['completed_at'] if last else '',
            total=total,last=dict(row) if row else None,errors=errors,error_labels=ERRORS,state='waiting',label='等待首次同步')
        if not result['enabled']:result.update(state='disabled',label='自动同步尚未启用')
        elif row:
            stamp=datetime.fromisoformat(row['completed_at'] or row['started_at'])
            age=(datetime.now(timezone.utc)-stamp).total_seconds()
            if row['status']=='failed':result.update(state='failed',label='同步待重试')
            elif age>180:result.update(state='delayed',label='同步延迟，请检查服务')
            elif row['status']=='running':result.update(state='running',label='正在同步')
            else:result.update(state='ready',label='自动同步正常')
        result['notifications']=self.notifications.summary()
        return result

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','batch','finish','status']);p.add_argument('--run',type=int)
    args=p.parse_args();sync=WebsiteSync()
    if args.action=='start':answer=dict(run=sync.start())
    elif args.action=='batch':
        raw=sys.stdin.buffer.read(2_000_001)
        if len(raw)>2_000_000:raise LeadError('INVALID_RECORD')
        answer=sync.ingest(json.loads(raw),args.run)
    elif args.action=='finish':answer=sync.finish(args.run,sys.stdin.read(100).strip())
    else:answer=sync.status()
    # Only counts, IDs and fixed codes go to the host log, never contact details.
    if args.action in {'finish','status'}:answer={k:v for k,v in answer.items() if k not in {'errors','error_labels','notifications'}}
    print(json.dumps(answer,ensure_ascii=True))

if __name__=='__main__':main()
