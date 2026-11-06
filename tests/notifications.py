"""Local receiver only: delivery, duplicate suppression, fixtures and retry queue."""
from pathlib import Path
import sys,tempfile,os,json,threading,uuid,dataclasses
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),str(Path(__file__).resolve().parents[1]/'geo')]
from common import ROOT
from geo_monitor.web.leads import LeadStore
from geo_monitor.web.website_inquiries import WebsiteSync
from geo_monitor.web.lead_notifications import LeadNotifications,NotifyConfig
checks=[];received=[]
def check(name,value):checks.append({'name':name,'passed':bool(value)});assert value,name
class Receiver(BaseHTTPRequestHandler):
 def do_POST(self):
  received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))));self.send_response(200);self.end_headers();self.wfile.write(b'{"ok":true}')
 def log_message(self,*args):pass
def main():
 server=ThreadingHTTPServer(('127.0.0.1',0),Receiver);threading.Thread(target=server.serve_forever,daemon=True).start()
 previous=os.environ.get('LEAD_DATA_DIR')
 try:
  with tempfile.TemporaryDirectory(prefix='notification-proof-',dir=ROOT/'.local') as directory:
   assert Path(directory).resolve().is_relative_to((ROOT/'.local').resolve());os.environ['LEAD_DATA_DIR']=directory;store=LeadStore('demo.example');cfg=NotifyConfig(webhook_url=f'http://127.0.0.1:{server.server_port}/',webhook_kind='generic',allow_test_http=True,origin='http://127.0.0.1:9322');notify=LeadNotifications(store,cfg);sync=WebsiteSync(store,notify)
   record=json.loads((ROOT/'examples/leads.json').read_text(encoding='utf-8'))[0];record['record_id']=str(uuid.uuid4());record['source_context'].pop('is_test');run=sync.start();sync.ingest([record],run);sync.finish(run)
   check('Configured local webhook delivers after insertion',len(received)==1)
   item=received[0];check('Notification contains buyer tags, attribution and GEO link',item['capsule_tags']['category'] and item['keyword'] and item['geo_url'].startswith('http://127.0.0.1:9322/leads/'))
   run=sync.start();sync.ingest([record],run);sync.finish(run);check('Repeat sync suppresses duplicate notifications',len(received)==1)
   record['record_id']=str(uuid.uuid4());record['source_context']['is_test']=True;run=sync.start();sync.ingest([record],run);sync.finish(run);check('Test fixture never sends notification',len(received)==1)
   notify.config=dataclasses.replace(cfg,webhook_url='http://127.0.0.1:1/');record['record_id']=str(uuid.uuid4());record['source_context'].pop('is_test');run=sync.start();sync.ingest([record],run);sync.finish(run)
   check('Delivery failure queued for retry without losing lead',notify.summary()['counts'].get('retry')==1 and len(store.all())==3)
   notify.config=NotifyConfig();check('Blank notification config silently disabled',notify.deliver_pending()['enabled'] is False)
 finally:
  server.shutdown();server.server_close()
  if previous is None:os.environ.pop('LEAD_DATA_DIR',None)
  else:os.environ['LEAD_DATA_DIR']=previous
if __name__=='__main__':
 try:main()
 finally:(ROOT/'reports/notifications.json').write_text(json.dumps(checks,indent=2));print(json.dumps({'passed':sum(x['passed'] for x in checks),'total':len(checks)}))
