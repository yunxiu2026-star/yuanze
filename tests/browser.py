"""Desktop/mobile EN/AR acceptance. Requires local services and dev dependencies."""
import os,sys,json,time
from pathlib import Path
if os.getenv('B2B_PLAYWRIGHT_PATH'):sys.path.insert(0,os.environ['B2B_PLAYWRIGHT_PATH'])
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import ROOT,runtime
from playwright.sync_api import sync_playwright,expect

checks=[];shots=[]
def check(name,value):checks.append({'name':name,'passed':bool(value)});assert value,name
def main():
 r=runtime();base=f'http://127.0.0.1:{r["ports"]["site"]}';geo=f'http://127.0.0.1:{r["ports"]["geo"]}';folder=ROOT/'reports/screenshots';folder.mkdir(parents=True,exist_ok=True)
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['B2B_BROWSER']} if os.getenv('B2B_BROWSER') else {}))
  for mode,width in [('desktop',1440),('mobile',390)]:
   context=browser.new_context(viewport={'width':width,'height':1000 if width>500 else 844},is_mobile=mode=='mobile',has_touch=mode=='mobile');page=context.new_page();errors=[];external=[]
   page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda req:external.append(req.url) if not req.url.startswith(('http://127.0.0.1:','data:')) else None)
   def visit(path):page.goto(base+path);page.wait_for_load_state('networkidle')
   def shot(name,full=False):
    file=name+'-'+mode+'.png';page.screenshot(path=str(folder/file),full_page=full);shots.append({'name':name+' '+mode,'path':'screenshots/'+file})
   def no_overflow(label):check(label,page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
   def fill(form,quote=False):
    for key,value in {'name':'Browser Example','company':'Fictional Acceptance','email':'browser@example.invalid','whatsapp':'+15555550110'}.items():form.locator(f'[name={key}]').fill(value)
    form.locator('[name=country]').select_option('United States');form.locator('[name=consent]').check()
    if quote:form.locator('[name=direction]').fill('Illustrative formulation and packaging project')
    form.locator('[data-chip-group=category]').first.click();form.locator('[data-chip-group=route]').first.click();form.locator('[data-chip-group=volume]').first.click();field=form.locator('[name=message]');field.fill(field.input_value()+'\nThis is an isolated browser acceptance request.')
   for lang,prefix in [('en',''),('ar','/ar')]:
    label=mode+' '+lang;visit(prefix+'/?utm_source=demo-search&utm_campaign=browser-check&utm_term=supplier&gclid=DEMO-BROWSER');no_overflow(label+' home width');check(label+' direction',page.locator('html').get_attribute('dir')==('rtl' if lang=='ar' else 'ltr'));shot('home-'+lang)
    if mode=='mobile':
     menu=page.locator('.menu-toggle');menu.click();check(label+' mobile menu opens',menu.get_attribute('aria-expanded')=='true');page.keyboard.press('Escape');check(label+' menu closes',menu.get_attribute('aria-expanded')=='false')
    else:
     chip=page.locator('.series [data-preview]').first;img=page.locator('.series-cover img').first;cover=img.get_attribute('src');chip.hover();check(label+' desktop hover image',img.get_attribute('src')==chip.get_attribute('data-preview'));page.locator('h1').hover();check(label+' hover restores cover',img.get_attribute('src')==cover)
    for i in range(4):
     guide=json.loads((ROOT/'config/guides.json').read_text(encoding='utf-8'))[i];visit(prefix+'/resources/'+guide['id']+'/');check(label+' guide table '+str(i),page.locator('table tbody tr').count()>1);no_overflow(label+' guide width '+str(i))
    shot('guide-'+lang)
    page.locator('[data-sample][data-guide]').click();form=page.locator('#sample-dialog form');check(label+' guide context',form.locator('[name=message]').input_value().startswith('[Buyer guide:'))
    fill(form);shot('sample-'+lang);before=page.evaluate('(window.dataLayer||[]).filter(x=>x.event==="generate_lead").length')
    with page.expect_response(lambda response:'/api/v1/sample-requests' in response.url) as response:form.locator('[type=submit]').click()
    result=response.value.json();check(label+' real sample receipt',response.value.status==201 and result.get('receipt_status')=='RECEIVED');expect(page.locator('#sample-dialog .request-success')).to_be_visible()
    check(label+' one conversion event',page.evaluate('(window.dataLayer||[]).filter(x=>x.event==="generate_lead").length')==before+1)
    check(label+' conversion idempotence',page.evaluate('(r)=>window.B2BAttribution.completeLead(r,"sample_request")',result) is False)
    check(label+' localized guide success link',page.locator('#sample-dialog [data-success-action=guides]').get_attribute('href')==prefix+'/resources/');check(label+' brand WhatsApp action',page.locator('#sample-dialog [data-success-action=whatsapp]').get_attribute('href').startswith('https://wa.me/15555550100?'))
    shot('received-'+lang);page.locator('.dialog-close').click()
    visit(prefix+'/products/');search=page.locator('#catalog-search');search.fill('serum');expect(page.locator('.product-card:visible')).to_have_count(1);check(label+' search count',page.locator('#catalog-count').inner_text().startswith('1') if lang=='en' else '1' in page.locator('#catalog-count').inner_text())
    if mode=='mobile':page.locator('.menu-toggle').click()
    page.locator('.language-link').click();page.wait_for_load_state('networkidle');check(label+' search survives language switch',page.locator('#catalog-search').input_value()=='serum');search=page.locator('#catalog-search');search.fill('');page.locator('a[data-family=body]').click();expect(page.locator('.product-card:visible')).to_have_count(2);check(label+' catalog category count',True)
    visit(prefix+'/products/');shot('products-'+lang);no_overflow(label+' catalog width')
    visit(prefix+'/get-a-quote/?brief=Supplier%20brief');form=page.locator('#quote-area form');check(label+' exported AEO prefill',form.locator('[name=message]').input_value().startswith('[Buyer guide: Supplier brief]'));check(label+' contact remembered',form.locator('[name=name]').input_value()=='Browser Example');fill(form,True);no_overflow(label+' quote width');shot('quote-'+lang)
    with page.expect_response(lambda response:'/api/v1/product-brief' in response.url) as response:form.locator('[type=submit]').click()
    check(label+' real product brief receipt',response.value.status==201 and response.value.json().get('receipt_status')=='RECEIVED');expect(page.locator('#quote-area .request-success')).to_be_visible()
    # Explicit network failure: retained data, visible error, no conversion event.
    page.locator('#quote-area [data-reset-request]').click();form.locator('[name=message]').fill('Network failure acceptance; do not submit.');count=page.evaluate('(window.dataLayer||[]).filter(x=>x.event==="generate_lead").length');page.route('**/api/v1/product-brief',lambda route:route.abort('failed'));form.locator('[type=submit]').click();expect(form.locator('.form-error')).to_be_visible();check(label+' failure retains data',form.locator('[name=message]').input_value().startswith('Network failure'));check(label+' failure does not emit lead',page.evaluate('(window.dataLayer||[]).filter(x=>x.event==="generate_lead").length')==count);page.unroute('**/api/v1/product-brief')
   page.goto(geo+'/login');page.wait_for_load_state('networkidle');page.locator('[name=password]').fill(r['admin_password']);page.locator('button[type=submit]').click();page.wait_for_load_state('networkidle')
   for name,path in [('geo-leads','/leads?kind=test'),('geo-indexing','/indexing'),('geo-events','/inquiries'),('geo-aeo','/optimize?draft_id=1'),('geo-password','/settings/password')]:
    page.goto(geo+path);page.wait_for_load_state('networkidle');check(mode+' '+name+' title',len(page.title())>0);no_overflow(mode+' '+name+' width');shot(name)
   check(mode+' no uncaught browser exceptions',not errors);check(mode+' no external tracking/provider calls',not external);context.close()
  context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce');page=context.new_page();page.goto(base+'/');page.wait_for_load_state('networkidle');page.locator('[data-stage-go="6"]').click();check('Reduced motion process navigation',page.locator('.process-frame.active').count()==1);context.close()
  context=browser.new_context(java_script_enabled=False,viewport={'width':390,'height':844});page=context.new_page();page.goto(base+'/get-a-quote/');check('No-JS quote skeleton and alternative contact',page.locator('form').first.is_visible() and page.locator('noscript a').is_visible());page.goto(base+'/ready-stock/');check('No-JS ready stock has static product cards',page.locator('.product-card').count()>0);context.close();browser.close()
if __name__=='__main__':
 try:main()
 finally:
  (ROOT/'reports/browser.json').write_text(json.dumps({'checks':checks,'screenshots':shots},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'passed':sum(x['passed'] for x in checks),'total':len(checks),'failed':[x['name'] for x in checks if not x['passed']]}))
