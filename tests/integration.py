"""Real local HTTP/SQLite acceptance, never contacts a customer host."""
import sys,json,re,uuid,time,sqlite3,subprocess
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import ROOT,runtime,environments
import httpx

checks=[]
def check(label,condition,detail=''):
 checks.append({'name':label,'passed':bool(condition),'detail':detail});assert condition,label+' '+str(detail)
def csrf(text):return re.search(r'name="csrf" value="([^"]+)"',text)[1]
def dbcount(file,table):
 with sqlite3.connect(file.as_uri()+'?mode=ro',uri=True) as db:return db.execute('SELECT count(*) FROM '+table).fetchone()[0]
def main():
 r=runtime();site=f'http://127.0.0.1:{r["ports"]["site"]}';geo=f'http://127.0.0.1:{r["ports"]["geo"]}';data=ROOT/'.local/data';client=httpx.Client(trust_env=False,timeout=30)
 routes=json.loads((ROOT/'dist/build.json').read_text())['routes']
 check('44 EN/AR static routes return 200',all(client.get(site+p).status_code==200 for p in routes),str(len(routes)))
 check('Demo routes excluded from indexing',all('noindex' in client.get(site+p).text for p in routes))
 check('Slash redirect preserves query',client.get(site+'/products?utm_source=demo').headers.get('location','').endswith('/products/?utm_source=demo'))
 check('Legacy resources redirect preserves query',client.get(site+'/knowledge/?x=1').headers.get('location')=='/resources/?x=1')
 check('Private API not exposed through site',client.post(site+'/api/v1/internal/records').status_code==404)
 payload={'source_route':'/products/','source_context':{'source_page':'/products/','landing_page':'/products/','first_landing_page':'/products/','gclid':'DEMO-CLICK','utm':{'source':'demo','medium':'cpc','campaign':'acceptance','term':'supplier'},'ad_source_tag':'[Ad Source: demo/acceptance | Keyword: supplier | Landing: /products/]'},'submitted_fields':{'name':'Integration Example','email':'integration@example.invalid','country':'United States','product':'Head care','message':'Company: Fictional Example WhatsApp: +15555550109 Sample request: [Brief: Head care | Custom development | 1,000 units] [Buyer guide: MOQ planning] [Ad Source: demo/acceptance | Keyword: supplier | Landing: /products/] This is an isolated acceptance record.','consent':True}}
 headers={'Origin':site,'Idempotency-Key':str(uuid.uuid4())};before=dbcount(data/'api/receipts.sqlite3','conversion_records');a=client.post(site+'/api/v1/sample-requests',json=payload,headers=headers)
 check('Sample intake durable receipt',a.status_code==201 and a.json().get('receipt_status')=='RECEIVED',str(a.status_code)+' '+a.text if a.status_code!=201 else '201 RECEIVED')
 b=client.post(site+'/api/v1/sample-requests',json=payload,headers=headers)
 check('Idempotent retry stores one receipt',b.json().get('record_id')==a.json().get('record_id') and dbcount(data/'api/receipts.sqlite3','conversion_records')==before+1)
 payload['submitted_fields']['consent']=False;bad=client.post(site+'/api/v1/sample-requests',json=payload,headers={'Origin':site,'Idempotency-Key':str(uuid.uuid4())})
 check('Missing consent rejected without write',bad.status_code==422 and dbcount(data/'api/receipts.sqlite3','conversion_records')==before+1)
 check('Untrusted browser origin rejected',client.post(site+'/api/v1/sample-requests',json=payload,headers={'Origin':'https://untrusted.invalid'}).status_code==403)
 for _ in range(25):
  with sqlite3.connect(data/'geo/customer-inquiries/leads.db') as db:found=db.execute('SELECT id FROM leads WHERE external_id=?',('web-'+a.json()['record_id'],)).fetchone()
  if found:break
  time.sleep(.5)
 check('Website receipt automatically arrives in GEO',bool(found))
 g=httpx.Client(base_url=geo,trust_env=False,timeout=30)
 check('GEO authentication required',g.get('/leads').status_code in {302,303,307})
 check('Generated local administrator login',g.post('/login',data={'password':r['admin_password'],'next':'/leads'}).status_code==303)
 paths=['/','/leads','/leads?kind=test','/leads/'+str(found[0]),'/indexing','/inquiries','/inquiries/export.csv','/optimize','/optimize?draft_id=1','/knowledge','/knowledge/new','/social','/social/meta','/ai','/analytics','/search-console','/google','/google/imports','/settings','/settings/password']
 for path in paths:check('Authenticated GEO '+path,g.get(path).status_code==200)
 detail=g.get('/leads/'+str(found[0])).text
 check('Brief, guide and advertising badges parsed',all(x in detail for x in ['Head care','MOQ planning','demo/acceptance','wa.me/15555550109']))
 count=dbcount(data/'geo/customer-inquiries/leads.db','leads');time.sleep(6)
 check('Repeated sync does not duplicate leads',dbcount(data/'geo/customer-inquiries/leads.db','leads')==count)
 page=g.get('/indexing');token=csrf(page.text);sync=g.post('/indexing/sitemap-sync',data={'csrf':token})
 check('Sitemap POST incrementally syncs real local site',sync.status_code==303)
 with sqlite3.connect(data/'geo/site-operations/operations.db') as db:
  actual={x[0] for x in db.execute('SELECT path FROM pages WHERE domain=?',('demo.example',))}
 check('All 44 sitemap routes monitored',actual==set(routes),str(len(actual)))
 again=g.post('/indexing/sitemap-sync',data={'csrf':token});check('Repeated sitemap sync adds zero',again.status_code==303 and '0' in again.headers['location'])
 technical=g.post('/indexing/1/technical',data={'csrf':token});check('Local HTTP/robots/canonical technical check',technical.status_code==303)
 with sqlite3.connect(data/'geo/site-operations/operations.db') as db:
  result=json.loads(db.execute("SELECT payload FROM checks WHERE kind='technical' ORDER BY id DESC LIMIT 1").fetchone()[0])
 check('Technical check records demo noindex truthfully',result.get('http_status')==200 and result.get('noindex') is True and result.get('sitemap') is True)
 for id in [1,2]:
  check('Unreviewed AEO draft export blocked '+str(id),g.get(f'/optimize/drafts/{id}/aeo.html').status_code==422)
  check('AEO draft explicit review '+str(id),g.post(f'/optimize/drafts/{id}/status',data={'csrf':token,'status':'reviewed'}).status_code==303)
  for fmt in ['html','json']:
   e=g.get(f'/optimize/drafts/{id}/aeo.{fmt}');check('Reviewed EN/AR AEO '+str(id)+' '+fmt,e.status_code==200 and 'FAQPage' in e.text)
   if fmt=='html':check('AEO sample hook routes to usable quote form '+str(id),'/get-a-quote/?brief=' in e.text)
  g.post(f'/optimize/drafts/{id}/status',data={'csrf':token,'status':'draft'})
 # Validate shorter password, old-session revocation and restore the private bootstrap password.
 old_cookie=dict(g.cookies);pwpage=g.get('/settings/password');token=csrf(pwpage.text)
 check('Password CSRF enforced',g.post('/settings/password',data={'old_password':r['admin_password'],'new_password':'Ab123456','confirm_password':'Ab123456'}).status_code==403)
 check('Wrong old password rejected',g.post('/settings/password',data={'csrf':token,'old_password':'wrong-old-password','new_password':'Ab123456','confirm_password':'Ab123456'}).status_code==422)
 changed=g.post('/settings/password',data={'csrf':token,'old_password':r['admin_password'],'new_password':'Ab123456','confirm_password':'Ab123456'});check('Eight-character replacement accepted',changed.status_code==303)
 stale=httpx.Client(base_url=geo,trust_env=False,cookies=old_cookie);check('Old sessions revoked',stale.get('/leads').status_code in {302,303,307})
 check('Old password no longer authenticates',g.post('/login',data={'password':r['admin_password']}).status_code==200)
 check('New password authenticates',g.post('/login',data={'password':'Ab123456'}).status_code==303)
 token=csrf(g.get('/settings/password').text)
 check('Test password restored through verified workflow',g.post('/settings/password',data={'csrf':token,'old_password':'Ab123456','new_password':r['admin_password'],'confirm_password':r['admin_password']}).status_code==303)
 files=list((data/'geo').rglob('*.db'));check('Nine GEO databases initialized',len(files)==9,str(len(files)))
 for p in files:
  with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:check('SQLite integrity '+p.relative_to(data).as_posix(),db.execute('PRAGMA integrity_check').fetchone()[0]=='ok')
 check('Business records preserved through password rotation',dbcount(data/'geo/customer-inquiries/leads.db','leads')==count)
 with sqlite3.connect(data/'geo/customer-inquiries/leads.db') as db:check('Unconfigured notification channels silent',db.execute('SELECT count(*) FROM website_notification_outbox').fetchone()[0]==0)

if __name__=='__main__':
 try:main()
 finally:
  (ROOT/'reports').mkdir(exist_ok=True);(ROOT/'reports/integration.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'passed':sum(x['passed'] for x in checks),'total':len(checks),'failed':[x['name'] for x in checks if not x['passed']]}))
