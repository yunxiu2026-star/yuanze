"""Online SQLite snapshots only. Restores should always target a fresh directory."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,sqlite3,json,shutil,os
from common import ROOT
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output');a=ap.parse_args();source=Path(os.getenv('B2B_DATA_ROOT',str(ROOT/'.local/data'))).resolve();target=Path(a.output or str(ROOT/'.local/backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))).resolve();assert not target.exists() and not target.is_relative_to(source)
 target.mkdir(parents=True);rows=[]
 for p in source.rglob('*'):
  if not p.is_file() or p.is_symlink() or p.name.endswith(('-wal','-shm','-journal','.lock')):continue
  q=target/p.relative_to(source);q.parent.mkdir(parents=True,exist_ok=True)
  with p.open('rb') as f:is_db=f.read(16)==b'SQLite format 3\x00'
  if is_db:
   with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(q) as dst:src.backup(dst);assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
   rows.append(str(p.relative_to(source)))
  else:shutil.copy2(p,q)
  q.chmod(0o600)
 private=target/'_configuration';private.mkdir()
 for p in [ROOT/'.local/runtime.json',ROOT/'.env.production']:
  if p.is_file():q=private/p.name;shutil.copy2(p,q);q.chmod(0o600)
 shutil.copytree(ROOT/'config',private/'config')
 (target/'manifest.json').write_text(json.dumps({'sqlite':rows,'source_modified':False,'cross_database_atomic':False},indent=2));print(f'Consistent per-database snapshot complete: {len(rows)} SQLite files. Private output: {target}')
if __name__=='__main__':main()
