"""Read committed source receipts and sync locally; never mounts a customer's site."""
from pathlib import Path
import os,sys,time,json,sqlite3,argparse,logging
from common import ROOT,environments

def sync_once():
 api,web=environments();os.environ.update(api);os.environ.update(web);sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'geo')]
 from geo_monitor.web.website_inquiries import WebsiteSync
 from geo_monitor.web.leads import LeadStore
 sync=WebsiteSync(LeadStore(web['TARGET_DOMAIN']));run=sync.start();total={'read':0,'added':0,'skipped':0,'failed':0}
 try:
  after=None
  while True:
   sql='SELECT record_id,record_type,created_at,source_route,source_context,submitted_fields FROM conversion_records WHERE deleted_at IS NULL AND deletion_requested_at IS NULL'
   args=[]
   if api['PERSISTENCE_BACKEND']=='sqlite':
    p=Path(api['SQLITE_DATABASE_PATH']);p.parent.mkdir(exist_ok=True,parents=True)
    if not p.exists():break
    conn=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=10);conn.row_factory=sqlite3.Row
    if after:sql+=' AND (created_at,record_id) > (?,?)';args=list(after)
    sql+=' ORDER BY created_at,record_id LIMIT 20';items=[dict(r) for r in conn.execute(sql,args)];conn.close()
   else:
    from runtime_foundation.configuration import connection_factory
    if after:sql+=' AND (created_at,record_id) > (%s,%s)';args=list(after)
    sql+=' ORDER BY created_at,record_id LIMIT 20'
    with connection_factory() as conn,conn.cursor() as cur:
     cur.execute('SET TRANSACTION READ ONLY');cur.execute(sql,args);items=[dict(zip(['record_id','record_type','created_at','source_route','source_context','submitted_fields'],r)) for r in cur.fetchall()]
   if not items:break
   for r in items:
    r['record_id']=str(r['record_id']);r['created_at']=r['created_at'].isoformat() if hasattr(r['created_at'],'isoformat') else r['created_at']
    for k in ['source_context','submitted_fields']:
     if isinstance(r[k],str):r[k]=json.loads(r[k])
   counts=sync.ingest(items,run)
   for k in total:total[k]+=counts[k]
   after=(items[-1]['created_at'],items[-1]['record_id'])
   if len(items)<20:break
  sync.finish(run);return total
 except Exception:
  sync.finish(run,'SYNC_FAILED');raise

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--once',action='store_true');ap.add_argument('--interval',type=float,default=60);a=ap.parse_args()
 if not a.once and a.interval<5:ap.error('Interval must be at least 5 seconds')
 # A process lock is held for the entire daemon / single run; a second worker exits.
 api,web=environments();root=Path(web['LEAD_DATA_DIR']);root.mkdir(exist_ok=True,parents=True);file=(root/'sync.lock').open('a+b');file.seek(0);file.write(b'0');file.flush();file.seek(0)
 try:
  if os.name=='nt':
   import msvcrt;msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
  else:
   import fcntl;fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError:print('Sync already running.');return
 while True:
  try:print(json.dumps({'ok':True,**sync_once()}),flush=True)
  except Exception as e:print(json.dumps({'ok':False,'error':'SYNC_FAILED'}),flush=True)
  if a.once:break
  time.sleep(a.interval)
if __name__=='__main__':main()
