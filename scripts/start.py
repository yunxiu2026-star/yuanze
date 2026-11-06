"""Native local supervisor. Uses only fresh .local/ data and loopback ports."""
from pathlib import Path
import sys,os,subprocess,time,signal,argparse,socket
from urllib.request import build_opener,ProxyHandler
from common import ROOT,environments,runtime
from build_site import build
from prepare_data import prepare

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--seed-demo',action='store_true');ap.add_argument('--sync-interval',type=int,default=60);a=ap.parse_args();r=runtime()
 for port in r['ports'].values():
  s=socket.socket();s.bind(('127.0.0.1',port));s.close()
 build();prepare(a.seed_demo);api,web=environments();processes=[];logs=[]
 def spawn(name,cmd,env,cwd):
  env={**env,'PYTHONPATH':str(ROOT/'geo')+os.pathsep+str(ROOT/'backend')};log=(ROOT/'.local'/(name+'.log')).open('a',encoding='utf-8');logs.append(log)
  p=subprocess.Popen(cmd,env=env,cwd=cwd,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0);processes.append(p)
 spawn('api',[sys.executable,'app.py'],api,ROOT/'backend')
 spawn('site',[sys.executable,str(ROOT/'scripts/serve_site.py')],api,ROOT)
 spawn('geo',[sys.executable,'-m','uvicorn','geo_monitor.web.app:create_app','--factory','--host','127.0.0.1','--port',str(r['ports']['geo']),'--no-access-log'],web,ROOT/'geo')
 opener=build_opener(ProxyHandler({}))
 try:
  for port,path in [(r['ports']['site'],'/'),(r['ports']['api'],'/api/v1/readiness'),(r['ports']['geo'],'/healthz')]:
   for i in range(100):
    if any(p.poll() is not None for p in processes):raise RuntimeError('A service stopped; inspect .local/*.log')
    try:
     if opener.open(f'http://127.0.0.1:{port}'+path,timeout=2).status==200:break
    except OSError:time.sleep(.2)
   else:raise RuntimeError('Local service readiness timed out')
  spawn('sync',[sys.executable,str(ROOT/'scripts/sync_leads.py'),'--interval',str(a.sync_interval)],api,ROOT)
  print(f'Site http://127.0.0.1:{r["ports"]["site"]}/ | GEO http://127.0.0.1:{r["ports"]["geo"]}/',flush=True);print('Generated login password: .local/access.txt | Stop: Ctrl+C',flush=True)
  while all(p.poll() is None for p in processes):time.sleep(1)
 except KeyboardInterrupt:pass
 finally:
  for p in processes:
   if p.poll() is None:p.terminate()
  for p in processes:
   try:p.wait(timeout=8)
   except subprocess.TimeoutExpired:p.kill()
  for log in logs:log.close()
if __name__=='__main__':main()
