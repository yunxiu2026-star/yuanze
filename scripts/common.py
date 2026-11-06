from pathlib import Path
from urllib.parse import urlsplit
import json,os,secrets

ROOT=Path(__file__).resolve().parents[1]
def load(name):return json.loads((ROOT/'config'/name).read_text(encoding='utf-8'))
def profile():return load('site.json')
def runtime():
 p=ROOT/'.local/runtime.json'
 if not p.is_file():raise RuntimeError('Run python scripts/init.py first.')
 return json.loads(p.read_text(encoding='utf-8'))
def environments():
 p=profile();r=runtime();data=Path(os.getenv('B2B_DATA_ROOT',str(ROOT/'.local/data'))).resolve();geo=data/'geo'
 local=os.getenv('B2B_MODE','local')=='local';front=f'http://127.0.0.1:{r["ports"]["site"]}' if local else p['public_origin'];dash=f'http://127.0.0.1:{r["ports"]["geo"]}' if local else p['dashboard_origin']
 common={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1','TZ':'Asia/Shanghai','TARGET_DOMAIN':p['domain'],'SITE_NAME':p['name'],'BRAND_TERMS':','.join(p['brand_terms']),'BUSINESS_KEYWORDS':','.join(p.get('business_keywords',[]))}
 api={**common,'APP_ENV':'LOCAL' if local else 'PRODUCTION','LOCAL_REVIEWER_TOKEN':r['reviewer_token'],'API_ALLOWED_ORIGINS':front,'PUBLIC_SITE_ORIGIN':p['public_origin'],'PERSISTENCE_BACKEND':os.getenv('PERSISTENCE_BACKEND','sqlite' if local else 'postgres'),'SQLITE_DATABASE_PATH':str(data/'api/receipts.sqlite3'),'PUBLIC_RATE_LIMIT':os.getenv('PUBLIC_RATE_LIMIT','60' if local else '5'),'PUBLIC_RATE_WINDOW_SECONDS':'60','BIND_HOST':'127.0.0.1','PORT':str(r['ports']['api']),'PRODUCT_BRIEF_SERIES':','.join(x['id'] for x in load('catalog.json')['collections'])+',unsure'}
 web={**common,'DATABASE_URL':'sqlite:///'+str(geo/'web-preview.db').replace('\\','/'),'QUESTIONS_FILE':str(geo/'questions.xlsx'),'REPORTS_DIR':str(geo/'reports'),'WEB_RELEASE_PROFILE':'growth-v2','WEB_DEPLOY_MODE':'rehearsal' if local else 'production','WEB_PREVIEW':'0','WEB_COOKIE_SECURE':'false' if local else 'true','WEB_PASSWORD':r['admin_password'],'WEB_SECRET':r['session_secret'],'WEB_PUBLIC_ORIGIN':dash,'WEB_AI_PAID_ENABLED':'false','ENGINES':'mock','WEBSITE_SYNC_ENABLED':'true','GOOGLE_REDIRECT_URI':dash+'/google/callback','META_REDIRECT_URI':dash+'/social/meta/callback','MAIN_SITE_INTERNAL_ORIGIN':os.getenv('MAIN_SITE_INTERNAL_ORIGIN',front),'OPENAI_API_KEY':'','GEMINI_API_KEY':'','PERPLEXITY_API_KEY':''}
 for key,sub in [('LEAD_DATA_DIR','customer-inquiries'),('SITE_OPERATIONS_DIR','site-operations'),('CONTENT_DATA_DIR','content-optimization'),('GOOGLE_PRIVATE_DIR','.google-private'),('GOOGLE_IMPORT_DIR','google-imports'),('AI_DATA_DIR','ai-monitor'),('AI_PRIVATE_DIR','.ai-private'),('META_PRIVATE_DIR','.meta-private'),('SOCIAL_DATA_DIR','social-media'),('KNOWLEDGE_DATA_DIR','knowledge-base')]:web[key]=str(geo/sub)
 # Only explicitly set operator secrets enable external services.
 for key in list(web):
  if key.startswith(('WEBSITE_NOTIFY_','SMTP_')):web.pop(key)
 for key,value in os.environ.items():
  if key.startswith(('WEBSITE_NOTIFY_','SMTP_')):web[key]=value
 web['WEB_AI_PAID_ENABLED']=os.getenv('WEB_AI_PAID_ENABLED','false')
 api['RFQ_CATEGORIES']=os.getenv('RFQ_CATEGORIES',','.join(c['name']['en'] for c in load('catalog.json')['collections'])+',Other / unsure')
 api['INQUIRY_EXPECTED_NOTIFY_TO']=os.getenv('INQUIRY_EXPECTED_NOTIFY_TO',p['email'])
 if os.getenv('B2B_ALLOW_EXTERNAL_ENGINES')=='1':
  web['ENGINES']=os.getenv('ENGINES','mock')
  for key in ['OPENAI_API_KEY','GEMINI_API_KEY','PERPLEXITY_API_KEY']:web[key]=os.getenv(key,'')
 return api,web
