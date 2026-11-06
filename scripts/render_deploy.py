from common import ROOT,profile,load
from urllib.parse import urlsplit
import re

p=profile();domain=p['domain'];geo=urlsplit(p['dashboard_origin']).hostname
if not all(re.fullmatch(r'[a-z0-9.-]+',x or '') for x in [domain,geo]):raise ValueError('Valid hostnames required')
out=ROOT/'deploy/generated';out.mkdir(exist_ok=True)
source=(ROOT/'deploy/nginx.conf.example').read_text();(out/'site.conf').write_text(source.replace('__GEO_DOMAIN__',geo).replace('__DOMAIN__',domain))
lines=[]
for source,target in load('redirects.json').items():
 if not re.fullmatch(r'/[a-z0-9/-]*',source) or not re.fullmatch(r'/[a-z0-9/-]*',target):raise ValueError('Only safe local redirects supported')
 lines.append(f'location = {source} {{ return 301 {target}$is_args$args; }}')
(out/'redirects.conf').write_text('\n'.join(lines)+'\n');print('Rendered deploy/generated/. Run nginx -t on target host before reload.')
