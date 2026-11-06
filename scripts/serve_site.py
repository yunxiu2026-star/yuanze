from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,unquote
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
from urllib.error import HTTPError
import json,os,sys
from common import ROOT,runtime,profile

class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'dist'),**kw)
 def log_message(self,*a):pass
 def end_headers(self):
  self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
  if not profile()['indexing_enabled']:self.send_header('X-Robots-Tag','noindex, nofollow')
  super().end_headers()
 def list_directory(self,path):self.send_error(404);return None
 def send_head(self):
  path=unquote(urlsplit(self.path).path)
  if any(x.startswith('.') for x in path.split('/') if x) or not Path(self.translate_path(self.path)).resolve().is_relative_to((ROOT/'dist').resolve()):self.send_error(404);return None
  target=json.loads((ROOT/'config/redirects.json').read_text()).get(path)
  if target:
   query=urlsplit(self.path).query;self.send_response(301);self.send_header('Location',target+('?' +query if query else ''));self.send_header('Content-Length','0');self.end_headers();return None
  return super().send_head()
 def do_POST(self):
  path=urlsplit(self.path).path
  if path not in {'/api/v1/product-brief','/api/v1/sample-requests','/api/v1/rfqs'}:self.send_error(404);return
  try:size=int(self.headers.get('Content-Length','0'))
  except ValueError:self.send_error(400);return
  if not 0<size<=65536:self.send_error(413);return
  body=self.rfile.read(size);origin=self.headers.get('Origin','');allowed=os.getenv('API_ALLOWED_ORIGINS',f'http://127.0.0.1:{runtime()["ports"]["site"]}').split(',')
  if origin and origin not in allowed:self.send_error(403);return
  headers={'Content-Type':self.headers.get('Content-Type','application/json'),'Idempotency-Key':self.headers.get('Idempotency-Key',''),'Origin':origin,'X-Forwarded-For':self.client_address[0]}
  url=os.getenv('API_INTERNAL_ORIGIN',f'http://127.0.0.1:{runtime()["ports"]["api"]}')+path
  try:
   try:r=build_opener(NoRedirect,ProxyHandler({})).open(Request(url,data=body,headers=headers),timeout=20)
   except HTTPError as e:r=e
   response=r.read(100000);self.send_response(r.status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
  except (OSError,TimeoutError):
   response=b'{"error_code":"intake_unavailable"}';self.send_response(503);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)

if __name__=='__main__':
 ThreadingHTTPServer((os.getenv('BIND_HOST','127.0.0.1'),int(os.getenv('SITE_PORT',str(runtime()['ports']['site'])))),Handler).serve_forever()
