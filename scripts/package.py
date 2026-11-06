"""Distribute committed source, safe screenshots and Git history. Never packages local state."""
from common import ROOT
from pathlib import Path
import subprocess,zipfile,io,json,hashlib,argparse

def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);args=ap.parse_args();output=Path(args.output).resolve()
 if output.exists():raise ValueError('Choose a new output path; existing deliveries are not overwritten')
 if git('status','--porcelain').strip():raise RuntimeError('Commit all source and public validation evidence before packaging')
 private=ROOT/'.local/export';private.mkdir(parents=True,exist_ok=True);bundle=private/'history.bundle'
 subprocess.run(['git','bundle','create',str(bundle),'--all'],cwd=ROOT,check=True,capture_output=True)
 subprocess.run(['git','bundle','verify',str(bundle)],cwd=ROOT,check=True,capture_output=True)
 output.parent.mkdir(parents=True,exist_ok=True);source=zipfile.ZipFile(io.BytesIO(git('archive','--format=zip','HEAD')));names=[]
 with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
  for row in source.infolist():
   if row.is_dir():continue
   path=Path(row.filename)
   if any(part in {'.local','.venv','.git','__pycache__'} for part in path.parts) or path.name.startswith('.env') and path.name!='.env.example' or path.suffix.lower() in {'.db','.sqlite','.xlsx','.log','.pem','.key'}:raise RuntimeError('Private or runtime artifact tracked in source')
   archive.writestr('b2b-site-starter/'+row.filename,source.read(row));names.append(row.filename)
  shots=json.loads((ROOT/'reports/browser.json').read_text(encoding='utf-8'))['screenshots']
  for item in shots:
   file=(ROOT/'reports'/item['path']).resolve()
   if not file.is_relative_to((ROOT/'reports/screenshots').resolve()) or file.suffix!='.png':raise ValueError('Unexpected screenshot path')
   archive.write(file,'b2b-site-starter/reports/'+item['path'])
  archive.write(bundle,'b2b-site-starter/git-history.bundle')
  archive.writestr('b2b-site-starter/DELIVERY.json',json.dumps({'version':'1.0.0','commit':git('rev-parse','HEAD').decode().strip(),'source_files':len(names),'screenshots':len(shots),'contains_customer_data':False,'contains_runtime_credentials':False,'docker_runtime_verified':False},indent=2))
 with zipfile.ZipFile(output) as archive:
  assert archive.testzip() is None
  assert not any('/.local/' in n or '/.venv/' in n or n.endswith(('.db','.sqlite','.log')) for n in archive.namelist())
 digest=hashlib.sha256(output.read_bytes()).hexdigest();output.with_suffix('.zip.sha256').write_text(digest+'  '+output.name+'\n',encoding='ascii')
 print(json.dumps({'package':str(output),'bytes':output.stat().st_size,'source_files':len(names),'screenshots':len(shots),'sha256':digest,'git_bundle_verified':True}))
if __name__=='__main__':main()
