"""Static Compose/Nginx contract checks. Explicitly not a container runtime test."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import ROOT
import yaml

checks=[]
def check(name,value):checks.append({'name':name,'passed':bool(value)});assert value,name
def main():
 base=yaml.safe_load((ROOT/'compose.yaml').read_text());prod=yaml.safe_load((ROOT/'compose.production.yaml').read_text())
 check('Compose YAML parses with service anchors',set(base['services'])=={'api','site','geo','sync'})
 check('All local service ports bind loopback',all(str(port).startswith('127.0.0.1:') for service in base['services'].values() for port in service.get('ports',[])))
 check('Sync source database mount is read-only',any(v.endswith(':/data/api:ro') for v in base['services']['sync']['volumes']))
 check('Site has no business database volume',not any('/data/' in v for v in base['services']['site']['volumes']))
 check('Containers use readonly roots and private runtime mounts',all(v['read_only'] and any('runtime.json:ro' in x for x in v['volumes']) for v in base['services'].values()))
 check('Production PostgreSQL matches retained version gate',prod['services']['postgres']['image']=='postgres:18')
 check('Production API requires explicit intake gates',prod['services']['api']['environment']['PRODUCTION_PUBLIC_INTAKE_ENABLED']=='1' and prod['services']['api']['environment']['PRODUCTION_SYNTHETIC_ONLY']=='0')
 check('No database port published',not prod['services']['postgres'].get('ports'))
 nginx=(ROOT/'deploy/generated/site.conf').read_text();redirects=(ROOT/'deploy/generated/redirects.conf').read_text()
 check('Nginx blocks internal API and checks slash paths','location /api/ { return 404; }' in nginx and 'try_files $uri $uri/ $uri/index.html =404;' in nginx)
 check('Nginx enforces per-IP intake limit','limit_req zone=b2b_intake' in nginx and 'proxy_set_header X-Forwarded-For $remote_addr;' in nginx)
 check('Generated redirects retain query strings','$is_args$args' in redirects and '__DOMAIN__' not in nginx)
if __name__=='__main__':
 try:main()
 finally:(ROOT/'reports/deployment.json').write_text(json.dumps({'checks':checks,'runtime_tested':False,'reason':'Docker, PostgreSQL and nginx are not installed on this machine.'},indent=2));print(json.dumps({'passed':sum(x['passed'] for x in checks),'total':len(checks),'runtime_tested':False}))
