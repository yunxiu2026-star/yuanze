import os,sys,json
from pathlib import Path
if os.getenv('B2B_PLAYWRIGHT_PATH'):sys.path.insert(0,os.environ['B2B_PLAYWRIGHT_PATH'])
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['B2B_BROWSER']} if os.getenv('B2B_BROWSER') else {}));page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:9320/');page.wait_for_load_state('networkidle');folder=ROOT/'reports/screenshots';folder.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(folder/'home-desktop-en.png'))
 print(json.dumps({'title':page.title(),'errors':errors,'samples':page.locator('[data-sample]').count(),'products':page.locator('.series').count(),'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'helper':page.evaluate('typeof window.B2BAttribution')},ensure_ascii=False))
 page.goto('http://127.0.0.1:9320/products/');page.wait_for_load_state('networkidle');print(json.dumps({'catalog':page.locator('#catalog-count').inner_text(),'family':[x.get_attribute('href') for x in page.locator('[data-family]').all()],'cards':page.locator('.product-card').count()}));browser.close()
