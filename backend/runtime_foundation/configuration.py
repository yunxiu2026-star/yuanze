"""Environment-only runtime bindings. Production activation remains fail-closed."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

MIN_POSTGRES_VERSION = 180000
TABLES = ('conversion_records', 'conversion_record_events', 'attachment_records',
          'notification_outbox', 'audit_events', 'submission_idempotency')


def required(name: str) -> str:
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError(f'missing_required_environment:{name}')
    return value


def environment() -> str:
    value = os.getenv('APP_ENV', 'LOCAL').upper()
    if value not in {'LOCAL', 'STAGING', 'PRODUCTION'}:
        raise RuntimeError('invalid_app_environment')
    return value


def require_staging() -> None:
    if environment() != 'STAGING':
        raise RuntimeError('staging_operation_only')


PRODUCTION_CHAIN_TASK = 'B2B-PRODUCTION-MINIMUM-INQUIRY-CHAIN-01'
PRODUCTION_SYNTHETIC_MARKER = 'B2B_PRODUCTION_MINIMUM_SYNTHETIC'


def public_intake_enabled() -> bool:
    return environment() == 'PRODUCTION' and os.getenv('PRODUCTION_PUBLIC_INTAKE_ENABLED', '0') == '1'


def require_production_chain() -> None:
    """Require an explicit intake mode and the isolated production database."""
    if environment() != 'PRODUCTION' or os.getenv('PRODUCTION_CHAIN_AUTHORIZATION') != PRODUCTION_CHAIN_TASK:
        raise RuntimeError('production_activation_not_authorized')
    public_flag = os.getenv('PRODUCTION_PUBLIC_INTAKE_ENABLED', '0')
    if public_flag not in {'0', '1'}:
        raise RuntimeError('invalid_production_intake_mode')
    expected_synthetic_flag = '0' if public_intake_enabled() else '1'
    if os.getenv('PRODUCTION_SYNTHETIC_ONLY') != expected_synthetic_flag:
        raise RuntimeError('production_intake_mode_conflict')
    if (os.getenv('PERSISTENCE_BACKEND') != 'postgres' or
            os.getenv('DB_NAME') != os.getenv('EXPECTED_DB_NAME', 'b2b_production') or
            os.getenv('DB_SCHEMA') != os.getenv('EXPECTED_DB_SCHEMA', 'b2b_production') or
            os.getenv('DB_USER') not in {'b2b_production_app','b2b_production_migrator'}):
        raise RuntimeError('independent_production_database_required')
    if os.getenv('API_ALLOWED_ORIGINS') != required('PUBLIC_SITE_ORIGIN'):
        raise RuntimeError('exact_production_origin_required')


def connection_factory():
    import psycopg
    schema = os.getenv('DB_SCHEMA', 'public')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema):
        raise RuntimeError('invalid_db_schema')
    host = required('DB_HOST')
    sslmode = os.getenv('DB_SSLMODE', 'verify-full')
    if environment() != 'LOCAL' and host not in {'127.0.0.1', 'localhost', 'postgres'}:
        if sslmode not in {'require', 'verify-ca', 'verify-full'}:
            raise RuntimeError('remote_database_tls_required')
    return psycopg.connect(host=host, port=int(os.getenv('DB_PORT', '5432')),
                          dbname=required('DB_NAME'), user=required('DB_USER'),
                          password=required('DB_PASSWORD'), sslmode=sslmode,
                          connect_timeout=5, options=f'-c search_path={schema}')


def migration_directory() -> Path:
    return Path(__file__).resolve().parents[1] / 'migrations'


def postgres_readiness() -> bool:
    from .migrations import MigrationRunner
    try:
        with connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::int")
            if cursor.fetchone()[0] < MIN_POSTGRES_VERSION:
                return False
            cursor.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
            if cursor.fetchone() != (False,False,False):
                return False
            for table in TABLES:
                cursor.execute('SELECT to_regclass(%s)', (table,))
                if not cursor.fetchone()[0]:
                    return False
            cursor.execute('SELECT name,sha256 FROM runtime_schema_migrations')
            applied = dict(cursor.fetchall())
            cursor.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE'),has_table_privilege('conversion_records','DELETE'),has_table_privilege('runtime_schema_migrations','UPDATE')")
            if any(cursor.fetchone()):
                return False
            if any(applied.get(item['name']) != item['sha256']
                   for item in MigrationRunner(migration_directory()).inventory()):
                return False
            cursor.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='notification_outbox' AND column_name IN ('delivery_status','attempt_count','locked_by','locked_at','next_attempt_at','last_attempt_at','last_error_code','processed_at')")
            if cursor.fetchone()[0] != 8:
                return False
            cursor.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='notification_outbox'::regclass AND conname IN ('outbox_delivery_status_check','outbox_attempt_count_check','outbox_sent_processed_check') AND convalidated")
            if cursor.fetchone()[0] != 3:
                return False
            cursor.execute("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND indexname IN ('idx_outbox_delivery_due','idx_outbox_processing_lease')")
            if cursor.fetchone()[0] != 2:
                return False
            cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid='conversion_record_events'::regclass AND tgname='conversion_events_append_only' AND tgenabled='O'")
            return cursor.fetchone()[0] == 1
    except Exception:
        return False


def allowed_origins() -> frozenset[str]:
    values = frozenset(v.strip() for v in required('API_ALLOWED_ORIGINS').split(',') if v.strip())
    if '*' in values or not values:
        raise RuntimeError('exact_origin_required')
    if environment() == 'STAGING' and len(values) != 1:
        raise RuntimeError('single_staging_origin_required')
    for origin in values:
        parsed = urlsplit(origin)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise RuntimeError('invalid_origin')
        loopback = parsed.hostname in {'127.0.0.1', 'localhost'}
        if not parsed.netloc or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback)):
            raise RuntimeError('https_or_loopback_origin_required')
    return values
