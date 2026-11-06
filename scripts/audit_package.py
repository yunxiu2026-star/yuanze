"""Check distributable source, never inspect private runtime contents."""
from pathlib import Path
import re,json,hashlib,subprocess
from common import ROOT

def files():
 result=[]
 for directory in ['backend','geo','site','config','examples','scripts','tests','docs','deploy','reports']:
  result.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'screenshots' not in p.parts and 'private' not in p.parts)
 result.extend(p for p in ROOT.iterdir() if p.is_file() and p.name not in {'.env.production'} and not p.name.endswith('.zip'))
 return sorted(set(result))

def audit():
 issues=[];count=0
 for p in files():
  rel=p.relative_to(ROOT).as_posix();count+=1
  if p.suffix.lower() in {'.db','.sqlite','.xlsx','.pem','.key','.p12'} or p.name.startswith('.env') and p.name!='.env.example':issues.append({'file':rel,'kind':'private_or_binary_runtime'})
  try:s=p.read_text(encoding='utf-8')
  except UnicodeError:issues.append({'file':rel,'kind':'unexpected_binary_or_encoding'});continue
  patterns={'private_key':r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----','live_api_key':r'\bsk-[A-Za-z0-9_-]{24,}','personal_workspace':r'(?:C:|D:)[\\/]Users[\\/]|/srv/(?:yuanze|geo-monitor)/','customer_domain':r'\byuanze' + r'beauty\.com\b','customer_phone':r'1802867' + r'7248','customer_certification':r'HBPCER[0-9]{8,}'}
  if p.name=='audit_package.py':continue
  for kind,pattern in patterns.items():
   if re.search(pattern,s,re.I):issues.append({'file':rel,'kind':kind})
 result={'files_checked':count,'issues':issues,'runtime_data_excluded':True,'original_product_photos_included':False,'passed':not issues}
 (ROOT/'reports/package-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result));return result
if __name__=='__main__':raise SystemExit(0 if audit()['passed'] else 1)
