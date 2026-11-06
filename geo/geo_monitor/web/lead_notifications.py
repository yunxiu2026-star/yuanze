"""Optional, bounded notifications for newly replicated real website inquiries.

Delivery never happens in the inquiry-insert transaction. No credentials or raw
provider responses are logged or stored. Tests use an isolated local receiver.
"""
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import smtplib
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
import uuid

LOG=logging.getLogger(__name__)
FIXTURE_IDS={f'{n}'*8+'-'+f'{n}'*4+'-4'+f'{n}'*3+'-8'+f'{n}'*3+'-'+f'{n}'*12 for n in [1,2,3,4]}

def clean(value,limit=240):
    return ' '.join(str(value or '').split())[:limit]

def clip_bytes(value,limit):return str(value).encode('utf-8')[:limit].decode('utf-8',errors='ignore')

@dataclass(frozen=True)
class NotifyConfig:
    webhook_url:str=''
    webhook_kind:str='auto'
    signing_secret:str=''
    smtp_host:str=''
    smtp_port:int=587
    smtp_user:str=''
    smtp_password:str=''
    smtp_from:str=''
    smtp_to:tuple=()
    smtp_mode:str='starttls'
    origin:str='http://127.0.0.1:9322'
    title:str='Demo Works · New website inquiry'
    allow_test_http:bool=False
    config_error:str=''

    @classmethod
    def from_env(cls):
        try:
            return cls(webhook_url=os.getenv('WEBSITE_NOTIFY_WEBHOOK_URL','').strip(),
                webhook_kind=os.getenv('WEBSITE_NOTIFY_WEBHOOK_KIND','auto').strip().lower(),
                signing_secret=os.getenv('WEBSITE_NOTIFY_WEBHOOK_SECRET',''),
                smtp_host=os.getenv('WEBSITE_NOTIFY_SMTP_HOST','').strip(),
                smtp_port=int(os.getenv('WEBSITE_NOTIFY_SMTP_PORT','587')),
                smtp_user=os.getenv('WEBSITE_NOTIFY_SMTP_USER',''),smtp_password=os.getenv('WEBSITE_NOTIFY_SMTP_PASSWORD',''),
                smtp_from=os.getenv('WEBSITE_NOTIFY_SMTP_FROM','').strip(),
                smtp_to=tuple(x.strip() for x in os.getenv('WEBSITE_NOTIFY_SMTP_TO','').split(',') if x.strip()),
                smtp_mode=os.getenv('WEBSITE_NOTIFY_SMTP_MODE','starttls').strip().lower(),
                origin=os.getenv('WEB_PUBLIC_ORIGIN','http://127.0.0.1:9322'), title=clean(os.getenv('WEBSITE_NOTIFY_TITLE',os.getenv('SITE_NAME','Demo Works')+' · New website inquiry'),80))
        except (ValueError,TypeError):return cls(config_error='NOTIFY_CONFIG_INVALID')

    def channels(self):
        channels=[]
        if self.webhook_url:
            try:
                p=urlsplit(self.webhook_url)
                valid=p.hostname and not p.username and not p.password and not p.fragment and (p.scheme=='https' or self.allow_test_http and p.scheme=='http' and p.hostname in {'localhost','127.0.0.1','::1'})
                if valid and self.webhook_kind in {'auto','generic','wecom','lark','dingtalk'}:channels.append('webhook')
            except ValueError:pass
        if self.smtp_host and 1<=self.smtp_port<=65535 and self.smtp_mode in {'starttls','ssl'} and 1<=len(self.smtp_to)<=10:
            addresses=(self.smtp_from,*self.smtp_to)
            if all('\r' not in x and '\n' not in x and re.fullmatch(r'[^\s@<>]+@[^\s@<>]+',parseaddr(x)[1]) for x in addresses):channels.append('smtp')
        return channels

    def kind(self):
        if self.webhook_kind!='auto':return self.webhook_kind
        host=urlsplit(self.webhook_url).hostname or ''
        if host=='qyapi.weixin.qq.com':return 'wecom'
        if host in {'open.feishu.cn','open.larksuite.com'}:return 'lark'
        if host=='oapi.dingtalk.com':return 'dingtalk'
        return 'generic'

class NotifyError(Exception):pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class LeadNotifications:
    def __init__(self,store,config=None,clock=None):
        self.store=store;self.config=config or NotifyConfig.from_env();self.clock=clock or time.time

    def schema(self,db):
        db.execute('''CREATE TABLE IF NOT EXISTS website_notification_outbox (
            id INTEGER PRIMARY KEY,lead_id INTEGER NOT NULL,channel TEXT NOT NULL,event_id TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
            due_at REAL NOT NULL,lease_until REAL NOT NULL DEFAULT 0,claim TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',sent_at REAL,UNIQUE(lead_id,channel))''')

    def enqueue(self,db,lead_id,record,receipt,tags):
        context=receipt.get('source_context',{})
        if record.get('record_type')!='customer' or receipt.get('record_id') in FIXTURE_IDS or any(context.get(k) is True for k in ['is_test','test_fixture','synthetic']):return
        channels=self.config.channels()
        if not channels:return
        utm=context.get('utm',{})
        if not isinstance(utm,dict):utm={}
        ad=' / '.join(clean(utm.get(k),120) for k in ['source','campaign'] if utm.get(k))
        ad=ad or clean(' · '.join(tags.get('ad_source',[])),300)
        keyword=clean(utm.get('term'),160)
        if not keyword:
            match=re.search(r'\bKeyword:\s*([^|\]]+)',ad,re.I)
            keyword=clean(match[1],160) if match else ''
        payload={'lead_id':int(lead_id),'name':clean(record.get('name'),120),'country':clean(record.get('country'),100),
            'capsule_tags':{k:[clean(v,240) for v in tags.get(k,[])[:3]] for k in ['category','route','volume','guide']},
            'ad_source':ad,'keyword':keyword,'landing_page':clean(context.get('first_landing_page') or context.get('landing_page'),240),
            'geo_url':self.config.origin+'/leads/'+str(int(lead_id))}
        for channel in channels:
            event='website-lead-'+str(int(lead_id))+'-'+channel
            data={**payload,'event':'website_inquiry.created','notification_id':event}
            db.execute('INSERT OR IGNORE INTO website_notification_outbox(lead_id,channel,event_id,payload,due_at) VALUES(?,?,?,?,?)',
                (lead_id,channel,event,json.dumps(data,ensure_ascii=False),self.clock()))

    def text(self,payload):
        tags=payload['capsule_tags'];brief=' | '.join(v for k in ['category','route','volume'] for v in tags.get(k,[]))
        values=[('Name',payload['name'],160),('Country',payload['country'],100),('Brief',brief,350),
            ('Buyer guide',' | '.join(tags.get('guide',[])),200),('Ad source',payload['ad_source'],220),
            ('Keyword',payload['keyword'],200),('Landing',payload['landing_page'],150)]
        return self.config.title+' #'+str(payload['lead_id'])+'\n'+'\n'.join(k+': '+clip_bytes(v or 'Not provided',limit) for k,v,limit in values)+'\nOpen in GEO: '+payload['geo_url']

    def webhook(self,payload):
        cfg=self.config;kind=cfg.kind();url=cfg.webhook_url;text=self.text(payload)
        if kind=='lark':body={'msg_type':'text','content':{'text':text}}
        elif kind in {'wecom','dingtalk'}:body={'msgtype':'text','text':{'content':text}}
        else:body={**payload,'text':text}
        if cfg.signing_secret and kind=='lark':
            stamp=str(int(self.clock()));body['timestamp']=stamp;body['sign']=base64.b64encode(hmac.new((stamp+'\n'+cfg.signing_secret).encode(),b'',hashlib.sha256).digest()).decode()
        if cfg.signing_secret and kind=='dingtalk':
            stamp=str(int(self.clock()*1000));sign=base64.b64encode(hmac.new(cfg.signing_secret.encode(),(stamp+'\n'+cfg.signing_secret).encode(),hashlib.sha256).digest()).decode();p=urlsplit(url);query=dict(parse_qsl(p.query));query.update(timestamp=stamp,sign=sign);url=urlunsplit((p.scheme,p.netloc,p.path,urlencode(query),''))
        request=Request(url,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','User-Agent':'B2BWebsiteLeadNotifier/1.0','X-B2B-Notification-ID':payload['notification_id']},method='POST')
        try:
            with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=3) as response:
                raw=response.read(32769)
                if len(raw)>32768:raise NotifyError('WEBHOOK_RESPONSE_TOO_LARGE')
                if response.status<200 or response.status>=300:raise NotifyError('WEBHOOK_HTTP_REJECTED')
            if kind!='generic':
                try:answer=json.loads(raw)
                except (ValueError,TypeError):raise NotifyError('WEBHOOK_ACK_INVALID') from None
                code=answer.get('code',answer.get('StatusCode')) if kind=='lark' else answer.get('errcode')
                if code not in [0,'0']:raise NotifyError('WEBHOOK_REJECTED')
        except (HTTPError,URLError,TimeoutError,socket.timeout,OSError):raise NotifyError('WEBHOOK_NETWORK_OR_HTTP_ERROR') from None

    def smtp(self,payload):
        cfg=self.config;message=EmailMessage();message['Subject']=cfg.title+' #'+str(payload['lead_id']);message['From']=cfg.smtp_from;message['To']=', '.join(cfg.smtp_to);message['Message-ID']='<'+payload['notification_id']+'@'+__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(os.getenv('WEB_PUBLIC_ORIGIN','https://geo.demo.example')).hostname+'>';message.set_content(self.text(payload))
        try:
            if cfg.smtp_mode=='ssl':connection=smtplib.SMTP_SSL(cfg.smtp_host,cfg.smtp_port,timeout=3,context=ssl.create_default_context())
            else:connection=smtplib.SMTP(cfg.smtp_host,cfg.smtp_port,timeout=3)
            with connection as client:
                if cfg.smtp_mode=='starttls':client.starttls(context=ssl.create_default_context())
                if cfg.smtp_user:client.login(cfg.smtp_user,cfg.smtp_password)
                if client.send_message(message):raise NotifyError('SMTP_RECIPIENT_REJECTED')
        except (smtplib.SMTPException,OSError,TimeoutError):raise NotifyError('SMTP_DELIVERY_FAILED') from None

    def deliver_pending(self,limit=10,budget=8):
        channels=self.config.channels();answer={'enabled':bool(channels),'sent':0,'retry':0,'failed':0}
        if not channels:return answer
        started=time.monotonic()
        for _ in range(limit):
            if time.monotonic()-started>=budget:break
            with self.store.database() as db:
                self.schema(db);db.execute('BEGIN IMMEDIATE');clock=self.clock()
                row=db.execute("SELECT * FROM website_notification_outbox WHERE channel IN ("+','.join('?' for _ in channels)+") AND attempts<5 AND ((state IN ('pending','retry') AND due_at<=?) OR (state='sending' AND lease_until<=?)) ORDER BY id LIMIT 1",(*channels,clock,clock)).fetchone()
                if not row:break
                row=dict(row);claim=uuid.uuid4().hex
                db.execute("UPDATE website_notification_outbox SET state='sending',attempts=attempts+1,lease_until=?,claim=? WHERE id=?",(clock+30,claim,row['id']))
            try:
                payload=json.loads(row['payload']);getattr(self,row['channel'])(payload);state='sent';error='';answer['sent']+=1
            except Exception as exc:
                # Only fixed codes escape; provider errors may contain private URLs or customer data.
                error=str(exc) if isinstance(exc,NotifyError) else 'NOTIFY_DELIVERY_FAILED';state='failed' if row['attempts']+1>=5 else 'retry';answer[state]+=1
            with self.store.database() as db:
                db.execute('UPDATE website_notification_outbox SET state=?,last_error=?,due_at=?,lease_until=0,sent_at=? WHERE id=? AND claim=?',
                    (state,error,self.clock()+min(3600,60*5**row['attempts']),self.clock() if state=='sent' else None,row['id'],claim))
        return answer

    def summary(self):
        with self.store.database() as db:
            self.schema(db);counts={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) n FROM website_notification_outbox GROUP BY state')}
        return {'enabled':bool(self.config.channels()),'channels':self.config.channels(),'counts':counts,'configuration_error':self.config.config_error}
