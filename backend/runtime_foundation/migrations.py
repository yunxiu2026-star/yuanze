from __future__ import annotations

import hashlib
from pathlib import Path


class MigrationRunner:
    """Applies ordinal SQL files through a caller-supplied DB-API connection."""

    def __init__(self, directory: Path):
        self.directory = directory

    def inventory(self) -> list[dict[str, str | int]]:
        result = []
        for path in sorted(self.directory.glob("[0-9][0-9][0-9]_*.sql")):
            data = path.read_bytes()
            result.append({"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return result

    def apply(self, connection) -> None:
        # Migration identity is durable, checksum-bound and serialized. Existing SQL
        # owns its BEGIN/COMMIT; do not silently reapply or nest its transactions.
        if not connection.autocommit:
            connection.commit()
            connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::int")
            if cursor.fetchone()[0] < 180000:
                raise RuntimeError('POSTGRES_VERSION_BLOCKED')
            cursor.execute('SELECT pg_advisory_lock(728361451)')
            try:
                cursor.execute('CREATE TABLE IF NOT EXISTS runtime_schema_migrations (name TEXT PRIMARY KEY, sha256 CHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)')
                cursor.execute('SELECT name,sha256 FROM runtime_schema_migrations')
                applied = dict(cursor.fetchall())
                for item in self.inventory():
                    if item['name'] in applied:
                        if applied[item['name']] != item['sha256']:
                            raise RuntimeError('migration_checksum_mismatch')
                        continue
                    sql = (self.directory / str(item['name'])).read_text(encoding='utf-8').strip()
                    if not sql.startswith('BEGIN;') or not sql.endswith('COMMIT;'):
                        raise RuntimeError('migration_transaction_required')
                    # Ledger insertion and migration are committed together.
                    try:
                        cursor.execute(sql[:-len('COMMIT;')])
                        cursor.execute('INSERT INTO runtime_schema_migrations(name,sha256) VALUES (%s,%s)', (item['name'], item['sha256']))
                        cursor.execute('COMMIT')
                    except Exception:
                        cursor.execute('ROLLBACK')
                        raise
            finally:
                cursor.execute('SELECT pg_advisory_unlock(728361451)')
