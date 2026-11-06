"""Container entrypoints; credentials come from a private runtime mount, never image layers."""
import os,sys
from common import ROOT,environments

name=sys.argv[1];api,web=environments();os.environ.update(web if name=='geo' else api)
os.environ['PYTHONPATH']=str(ROOT/'geo')+os.pathsep+str(ROOT/'backend')
if name=='site':
 os.environ.update(BIND_HOST='0.0.0.0',SITE_PORT='8080');os.execv(sys.executable,[sys.executable,str(ROOT/'scripts/serve_site.py')])
elif name=='api':
 os.chdir(ROOT/'backend');os.execv(sys.executable,[sys.executable,'-m','gunicorn','--bind=0.0.0.0:8080','--workers=2','--threads=4','--timeout=30','app:application'])
elif name=='geo':
 from prepare_data import prepare
 prepare(False);os.chdir(ROOT/'geo');os.execv(sys.executable,[sys.executable,'-m','uvicorn','geo_monitor.web.app:create_app','--factory','--host','0.0.0.0','--port','8000','--no-access-log'])
elif name=='sync':os.execv(sys.executable,[sys.executable,str(ROOT/'scripts/sync_leads.py'),'--interval','60'])
else:raise SystemExit('Unknown service')
