"""Explicit provisioning for a fresh operator-owned DB; never deletes business data."""
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import psycopg
from psycopg import sql
from runtime_foundation.migrations import MigrationRunner

def main():
 password=os.environ['APP_DB_PASSWORD']
 if not password:raise ValueError('APP_DB_PASSWORD required')
 with psycopg.connect(host=os.getenv('DB_HOST','postgres'),dbname='b2b_production',user='postgres',password=os.environ['POSTGRES_PASSWORD'],sslmode=os.getenv('DB_SSLMODE','disable'),autocommit=True) as db,db.cursor() as cur:
  for role in ['b2b_production_app','b2b_production_migrator']:
   cur.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,))
   if not cur.fetchone():cur.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Identifier(role)))
  cur.execute(sql.SQL('ALTER ROLE b2b_production_app LOGIN PASSWORD {}').format(sql.Literal(password)))
  cur.execute('CREATE SCHEMA IF NOT EXISTS b2b_production AUTHORIZATION b2b_production_migrator')
  cur.execute('SET ROLE b2b_production_migrator');cur.execute('SET search_path=b2b_production')
  MigrationRunner(Path(__file__).resolve().parents[1]/'backend/migrations').apply(db)
  cur.execute('GRANT USAGE ON SCHEMA b2b_production TO b2b_production_app')
  cur.execute('GRANT SELECT ON ALL TABLES IN SCHEMA b2b_production TO b2b_production_app')
  for table in ['conversion_records','conversion_record_events','attachment_records','notification_outbox','audit_events','submission_idempotency']:
   cur.execute(sql.SQL('GRANT INSERT ON {} TO b2b_production_app').format(sql.Identifier(table)))
  for table in ['conversion_records','attachment_records','notification_outbox','submission_idempotency']:
   cur.execute(sql.SQL('GRANT UPDATE ON {} TO b2b_production_app').format(sql.Identifier(table)))
 print('Migration checksums recorded; application role has no DELETE, DDL or ledger UPDATE.')
if __name__=='__main__':main()
