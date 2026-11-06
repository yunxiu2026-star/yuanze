from pathlib import Path
import json,hashlib,shutil,sys,re
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element,SubElement,tostring
from jinja2 import Environment,FileSystemLoader,select_autoescape,StrictUndefined
from common import ROOT,load,profile

def build():
 site=profile();catalog=load('catalog.json');guides=load('guides.json');pages=load('pages.json');out=ROOT/'dist';out.mkdir(exist_ok=True)
 if not re.fullmatch(r'[a-zA-Z0-9.-]+',site['domain']) or site['public_origin']!='https://'+site['domain']:raise ValueError('Use a bare domain and matching HTTPS public_origin.')
 if site['indexing_enabled'] and (site['demo'] or site['domain'].endswith(('.example','.test','.invalid'))):raise ValueError('Example sites cannot be published as indexable.')
 if site['analytics'].get('ga4_id') and site['analytics'].get('gtm_id'):raise ValueError('Choose direct GA4 or GTM to avoid duplicate conversion reporting.')
 previous=json.loads((out/'build.json').read_text())['routes'] if (out/'build.json').exists() else []
 ids=set()
 for p in catalog['products']:
  if not re.fullmatch(r'[a-z0-9-]+',p['id']) or p['id'] in ids:raise ValueError('Unique URL-safe product id required')
  ids.add(p['id']);assert p['collection'] in {c['id'] for c in catalog['collections']}
 for p in catalog['products']:
  if not (ROOT/'site/static'/p['image'].removeprefix('/assets/')).is_file():raise ValueError('Missing image: '+p['image'])
 env=Environment(loader=FileSystemLoader(ROOT/'site/templates'),autoescape=select_autoescape(['html']),undefined=StrictUndefined)
 def route(path,lang):return ('/ar' if lang=='ar' else '')+path
 def tr(v,lang):return v.get(lang,v.get('en','')) if isinstance(v,dict) else v
 env.globals.update(route=route,tr=tr)
 routes=[];built=[]
 base=[('/','home',None),('/products/','products',None),('/ready-stock/','stock',None),('/resources/','resources',None),('/get-a-quote/','quote',None),('/contact/','contact',None),('/packaging/','packaging',None),('/product-development/','process',None),('/quality-control/','quality',None),('/about/','about',None),('/privacy-policy/','privacy',None),('/terms-of-use/','terms',None)]
 base += [('/products/'+p['id']+'/','detail',p) for p in catalog['products']]
 base += [('/resources/'+g['id']+'/','guide',g) for g in guides]
 for lang in site['languages']:
  for path,kind,item in base:
   localized=route(path,lang);dest=out/localized.lstrip('/')/'index.html';dest.parent.mkdir(parents=True,exist_ok=True)
   title=tr(item['title'],lang) if item else ({'home':site['name'],'products':'Products','stock':'Ready stock','resources':'Buyer decision guides','quote':'Project brief','contact':'Contact','packaging':'Packaging','process':'Product development','quality':'Quality requirements','about':'About','privacy':'Privacy notice','terms':'Terms of use'}[kind])
   faq=[[tr(q,lang),tr(a,lang)] for q,a in item.get('faq',[])] if kind=='guide' else [[tr(q['q'],lang),tr(q['a'],lang)] for q in pages['faq']] if kind=='home' else []
   schema={'@context':'https://schema.org','@graph':[{'@type':'Organization','@id':site['public_origin']+'/#organization','name':site['name'],'url':site['public_origin']+'/','email':site['email']}]}
   if faq:schema['@graph'].append({'@type':'FAQPage','inLanguage':lang,'mainEntity':[{'@type':'Question','name':q,'acceptedAnswer':{'@type':'Answer','text':a}} for q,a in faq]})
   content=env.get_template('page.html').render(site=site,catalog=catalog,guides=guides,pages=pages,lang=lang,path=path,kind=kind,item=item,title=title,canonical=site['public_origin']+localized,faq=faq,schema=schema)
   dest.write_text(content,encoding='utf-8');routes.append(localized);built.append(dest.relative_to(out).as_posix())
 (out/'assets').mkdir(exist_ok=True)
 for p in (ROOT/'site/static').iterdir():
  if p.is_file():shutil.copy2(p,out/'assets'/p.name)
 sitemap=Element('urlset',xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
 for r in routes:SubElement(SubElement(sitemap,'url'),'loc').text=site['public_origin']+r
 (out/'sitemap.xml').write_bytes(tostring(sitemap,encoding='utf-8',xml_declaration=True));(out/'robots.txt').write_text('User-agent: *\n'+('Allow: /\n' if site['indexing_enabled'] else 'Disallow: /\n')+'Sitemap: '+site['public_origin']+'/sitemap.xml\n')
 (out/'build.json').write_text(json.dumps({'brand':site['name'],'domain':site['domain'],'routes':routes,'demo':site['demo'],'indexing':site['indexing_enabled']},indent=2));print(f'Built {len(routes)} routes for {site["name"]}.')
 for old in set(previous)-set(routes):
  stale=(out/old.lstrip('/')/'index.html').resolve()
  if not stale.is_relative_to(out.resolve()):raise ValueError('Invalid previous build path')
  stale.unlink(missing_ok=True)
 return routes
if __name__=='__main__':build()
