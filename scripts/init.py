"""Create fresh local secrets and editable demo runtime, never import client data."""
from pathlib import Path
import argparse,json,secrets,sys,os
from common import ROOT,profile

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--site-port',type=int,default=9320);ap.add_argument('--api-port',type=int,default=9321);ap.add_argument('--geo-port',type=int,default=9322);args=ap.parse_args()
 p=ROOT/'.local';p.mkdir(exist_ok=True);file=p/'runtime.json'
 if not file.exists():
  value={'ports':{'site':args.site_port,'api':args.api_port,'geo':args.geo_port},'admin_password':secrets.token_urlsafe(18),'session_secret':secrets.token_hex(32),'reviewer_token':secrets.token_urlsafe(32)}
  file.write_text(json.dumps(value,indent=2));file.chmod(0o600)
 r=json.loads(file.read_text());note=p/'access.txt';note.write_text(f'Local demonstration only\nSite: http://127.0.0.1:{r["ports"]["site"]}/\nGEO: http://127.0.0.1:{r["ports"]["geo"]}/\nAdmin password: {r["admin_password"]}\n\nThis file is private and excluded from Git and delivery packages.\n');note.chmod(0o600)
 print('Local configuration ready. Addresses and generated password: .local/access.txt')
if __name__=='__main__':main()
