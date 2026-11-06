from pathlib import Path
import os,sys,json
from common import ROOT,environments

def prepare(seed=False):
 _,web=environments();os.environ.update(web);sys.path.insert(0,str(ROOT/'geo'))
 from geo_monitor.config import Config
 from geo_monitor.db import DB
 from geo_monitor.web.operations_store import OperationsStore
 from geo_monitor.web.leads import LeadStore
 from geo_monitor.web.website_inquiries import WebsiteSync
 from geo_monitor.web.google_imports import ImportStore
 from geo_monitor.web.content_optimization import DraftStore
 from geo_monitor.web.knowledge import KnowledgeStore
 from geo_monitor.web.social import SocialStore
 from geo_monitor.web.ai_monitor import AIMonitor
 from openpyxl import Workbook
 cfg=Config();db=DB(cfg.database_url);db.init();db.close();folder=Path(cfg.database_url[10:]).parent
 db=DB('sqlite:///'+str(folder/'demo.db').replace('\\','/'));db.init();db.close()
 ops=OperationsStore(cfg.target_domain,cfg.database_url);store=LeadStore(cfg.target_domain);sync=WebsiteSync(store)
 with store.database() as d:sync.schema(d)
 ImportStore().history();DraftStore().list();KnowledgeStore().categories();SocialStore(cfg.target_domain).accounts()
 with AIMonitor(cfg).database():pass
 q=Path(cfg.questions_file)
 if not q.exists():
  wb=Workbook();ws=wb.active;ws.title='监测问题';ws.append(['ID','语言','目标市场','问题类型','监测问题 (Prompt)','中文释义','优先级','期望被引用页面','启用']);ws.append(['D001','英语','美国','采购决策','How should a buyer prepare a supplier brief?','采购前应准备什么资料','P1','https://'+cfg.target_domain+'/resources/','是']);wb.save(q)
 if seed:
  records=json.loads((ROOT/'examples/leads.json').read_text(encoding='utf-8'));run=sync.start();sync.ingest(records,run);sync.finish(run)
  drafts=DraftStore()
  if not drafts.list():
   for row in json.loads((ROOT/'examples/drafts.json').read_text(encoding='utf-8')):drafts.save(row)
  from geo_monitor.web.google_data import GoogleData
  from geo_monitor.web.inquiries import parse_records
  from datetime import datetime,timedelta
  from zoneinfo import ZoneInfo
  google=GoogleData()
  if not google.settings():google.write('settings.json',{'property':'000000000','demo':True})
  if google.settings().get('demo'):
   stamp=(datetime.now(ZoneInfo(cfg.timezone)).date()-timedelta(days=1)).isoformat()
   content=(ROOT/'examples/ga4-events.csv').read_bytes();payload=parse_records('ga4','FICTIONAL-DEMO.csv',content,stamp,stamp,cfg.target_domain,cfg.timezone)
   ops.save_batch('ga4','000000000','FICTIONAL-DEMO-NOT-GOOGLE.csv',stamp,stamp,payload['digest'],payload)
 print('Fresh application stores ready. Example data: '+str(seed))
if __name__=='__main__':prepare('--seed-demo' in sys.argv)
