"""A separate temporary brand build proves configuration is reusable."""
from pathlib import Path
import sys,json,tempfile,shutil,os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import common,build_site
ROOT=common.ROOT;checks=[]
def check(name,value):checks.append({'name':name,'passed':bool(value)});assert value,name

def main():
 with tempfile.TemporaryDirectory(prefix='brand-proof-',dir=ROOT/'.local') as directory:
  temp=Path(directory).resolve();assert temp.is_relative_to((ROOT/'.local').resolve())
  shutil.copytree(ROOT/'config',temp/'config');shutil.copytree(ROOT/'site',temp/'site');(temp/'.local').mkdir();shutil.copy2(ROOT/'.local/runtime.json',temp/'.local/runtime.json')
  common.ROOT=temp;build_site.ROOT=temp
  try:
   p=common.profile();p.update(name='North Harbor Supply',domain='north-harbor.example',public_origin='https://north-harbor.example',dashboard_origin='https://desk.north-harbor.example',email='sales@north-harbor.example',whatsapp='15555550999',brand_terms=['North Harbor'],business_keywords=['components','supplier'])
   (temp/'config/site.json').write_text(json.dumps(p),encoding='utf-8');catalog=common.load('catalog.json');catalog['collections'][0]['id']='components'
   for item in catalog['products']:
    if item['collection']=='head':item['collection']='components'
   catalog['products'][0]['id']='example-component';(temp/'config/catalog.json').write_text(json.dumps(catalog),encoding='utf-8')
   routes=build_site.build();html=(temp/'dist/index.html').read_text(encoding='utf-8');ar=(temp/'dist/ar/index.html').read_text(encoding='utf-8');api,web=common.environments()
   check('New brand replaces heading and canonical','North Harbor Supply' in html and 'https://north-harbor.example/' in html and 'Demo Works' not in html)
   check('New product id generates EN/AR details','/products/example-component/' in routes and '/ar/products/example-component/' in routes)
   check('Collection config drives quote API series','components' in api['PRODUCT_BRIEF_SERIES'])
   check('Domain drives GEO and WhatsApp config',web['TARGET_DOMAIN']=='north-harbor.example' and '15555550999' in html)
   check('Arabic direction survives rebrand','dir="rtl"' in ar)
   catalog['products']=catalog['products'][1:];(temp/'config/catalog.json').write_text(json.dumps(catalog),encoding='utf-8');build_site.build()
   check('Removed product stale HTML is cleaned',not (temp/'dist/products/example-component/index.html').exists())
   p['indexing_enabled']=True;(temp/'config/site.json').write_text(json.dumps(p),encoding='utf-8')
   try:build_site.build();blocked=False
   except ValueError:blocked=True
   check('Demo indexing activation fails closed',blocked)
  finally:common.ROOT=ROOT;build_site.ROOT=ROOT
 # The temporary directory and copied runtime secret have been removed.
 check('Rebrand proof leaves original config and private runtime intact',common.profile()['name']=='Demo Works' and (ROOT/'.local/runtime.json').is_file())
if __name__=='__main__':
 try:main()
 finally:(ROOT/'reports/configuration.json').write_text(json.dumps(checks,indent=2));print(json.dumps({'passed':sum(x['passed'] for x in checks),'total':len(checks)}))
