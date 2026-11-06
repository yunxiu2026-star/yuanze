from __future__ import annotations

import copy
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .notifications import SAFE_DELIVERY_ERRORS
from .operations import NotificationOperationError, safe_delivery_error, validate_requeue

try:
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # LOCAL SQLite runtime does not require the PostgreSQL driver.
    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, _value):
            raise RuntimeError("psycopg_required_for_postgres_repository")

from .models import (
    AttachmentRecord,
    ConversionRecord,
    RecordStatus,
    RecordType,
    ScanStatus,
    StaleStatusUpdate,
    canonical_timestamp,
    require_status_transition,
    utc_now,
)


class InMemoryTestRepository:
    """Deterministic isolated repository used by tests; never production data."""

    def __init__(self) -> None:
        self.records: dict[str, ConversionRecord] = {}
        self.events: list[dict[str, Any]] = []
        self.audit_events: list[dict[str, Any]] = []
        self.attachments: dict[str, AttachmentRecord] = {}
        self.outbox: list[dict[str, Any]] = []
        self.idempotency: dict[str, str] = {}
        self._lock = threading.RLock()
        self.fail_after_record = False

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            snapshot = copy.deepcopy((self.records, self.events, self.audit_events, self.attachments, self.outbox, self.idempotency))
            try:
                yield
            except Exception:
                self.records, self.events, self.audit_events, self.attachments, self.outbox, self.idempotency = snapshot
                raise

    def create_atomic(self, record: ConversionRecord, idempotency_key: str, event: dict[str, Any], outbox: dict[str, Any]) -> ConversionRecord:
        with self.transaction():
            existing = self.idempotency.get(idempotency_key)
            if existing:
                return self.records[existing]
            self.records[record.record_id] = record
            if self.fail_after_record:
                raise RuntimeError("synthetic_transaction_failure")
            self.events.append(event)
            self.audit_events.append({"event_id": event["event_id"], "timestamp": event["timestamp"], "actor_type": event["actor_type"], "actor_id": event["actor_id"], "record_id": record.record_id, "action": event["action"], "safe_metadata": {"record_type": record.record_type.value}})
            self.outbox.append(outbox)
            self.idempotency[idempotency_key] = record.record_id
            return record

    def list_records(self) -> list[ConversionRecord]:
        return [r for r in self.records.values() if r.deleted_at is None]

    def get(self, record_id: str) -> ConversionRecord | None:
        record = self.records.get(record_id)
        return record if record and record.deleted_at is None else None

    def update_status(self, record_id: str, status: RecordStatus, actor_id: str, expected_status: RecordStatus | None = None) -> ConversionRecord:
        with self.transaction():
            record = self.records[record_id]
            before = record.status
            if expected_status is not None and before != expected_status:
                raise StaleStatusUpdate("stale_status_update")
            require_status_transition(before, status)
            if before == status:
                return record
            record.status = status
            self.events.append({"event_id": f"evt_{len(self.events)+1}", "timestamp": utc_now(), "actor_type": "STAFF", "actor_id": actor_id, "record_id": record_id, "action": "status_changed", "metadata": {"from": before.value, "to": status.value}})
            self.audit_events.append({"event_id": f"audit_{len(self.audit_events)+1}", "timestamp": utc_now(), "actor_type": "STAFF", "actor_id": actor_id, "record_id": record_id, "action": "status_changed", "safe_metadata": {"from": before.value, "to": status.value}})
            return record

    def add_attachment(self, attachment: AttachmentRecord) -> None:
        if attachment.record_id not in self.records:
            raise KeyError("attachment_owner_missing")
        self.attachments[attachment.attachment_id] = attachment
        self.audit_events.append({"event_id": f"audit_{len(self.audit_events)+1}", "timestamp": utc_now(), "actor_type": "PUBLIC", "actor_id": None, "record_id": attachment.record_id, "action": "attachment_received", "safe_metadata": {"attachment_id": attachment.attachment_id, "mime_type": attachment.mime_type, "size": attachment.size, "scan_status": attachment.scan_status.value}})

    def get_attachment(self, attachment_id: str) -> AttachmentRecord | None:
        return self.attachments.get(attachment_id)

    def list_attachments(self, record_id: str) -> list[AttachmentRecord]:
        return [item for item in self.attachments.values() if item.record_id == record_id]

    def export_logical_backup(self) -> dict[str, Any]:
        return copy.deepcopy({"records": self.records, "events": self.events, "audit_events": self.audit_events, "attachments": self.attachments, "outbox": self.outbox, "idempotency": self.idempotency})

    def restore_logical_backup(self, backup: dict[str, Any]) -> None:
        if self.records or self.events or self.audit_events or self.attachments or self.outbox:
            raise RuntimeError("restore_target_not_empty")
        self.records = copy.deepcopy(backup["records"])
        self.events = copy.deepcopy(backup["events"])
        self.audit_events = copy.deepcopy(backup["audit_events"])
        self.attachments = copy.deepcopy(backup["attachments"])
        self.outbox = copy.deepcopy(backup["outbox"])
        self.idempotency = copy.deepcopy(backup["idempotency"])


class SqliteRepository:
    """Durable LOCAL adapter for the accepted runtime contract.

    Production remains PostgreSQL-only. This adapter exists so an isolated local
    candidate can prove real HTTP persistence without installing a second server.
    """

    SCHEMA = """
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS conversion_records (
      record_id TEXT PRIMARY KEY,
      record_type TEXT NOT NULL,
      created_at TEXT NOT NULL,
      source_route TEXT NOT NULL,
      source_context TEXT NOT NULL,
      submitted_fields TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'NEW',
      retention_class TEXT NOT NULL DEFAULT 'UNDECIDED',
      deletion_requested_at TEXT,
      deleted_at TEXT
    );
    CREATE TABLE IF NOT EXISTS conversion_record_events (
      event_id TEXT PRIMARY KEY,
      timestamp TEXT NOT NULL,
      actor_type TEXT NOT NULL,
      actor_id TEXT,
      record_id TEXT NOT NULL REFERENCES conversion_records(record_id),
      action TEXT NOT NULL,
      metadata TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS attachment_records (
      attachment_id TEXT PRIMARY KEY,
      record_id TEXT NOT NULL REFERENCES conversion_records(record_id),
      safe_storage_key TEXT NOT NULL UNIQUE,
      original_filename TEXT NOT NULL,
      safe_filename TEXT NOT NULL,
      mime_type TEXT NOT NULL,
      size INTEGER NOT NULL,
      hash TEXT NOT NULL,
      scan_status TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS notification_outbox (
      outbox_id TEXT PRIMARY KEY,
      record_id TEXT NOT NULL REFERENCES conversion_records(record_id),
      event_type TEXT NOT NULL,
      payload TEXT NOT NULL,
      created_at TEXT NOT NULL,
      processed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS audit_events (
      event_id TEXT PRIMARY KEY,
      timestamp TEXT NOT NULL,
      actor_type TEXT NOT NULL,
      actor_id TEXT,
      record_id TEXT REFERENCES conversion_records(record_id),
      action TEXT NOT NULL,
      safe_metadata TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS submission_idempotency (
      idempotency_key TEXT PRIMARY KEY,
      record_id TEXT NOT NULL UNIQUE REFERENCES conversion_records(record_id),
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_local_conversion_created ON conversion_records(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_local_outbox_pending ON notification_outbox(processed_at,created_at);
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(self.SCHEMA)

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _record(row) -> ConversionRecord:
        return ConversionRecord(
            record_id=str(row["record_id"]), record_type=RecordType(str(row["record_type"])),
            created_at=str(row["created_at"]), source_route=str(row["source_route"]),
            source_context=json.loads(row["source_context"]), submitted_fields=json.loads(row["submitted_fields"]),
            status=RecordStatus(str(row["status"])), retention_class=str(row["retention_class"]),
            deletion_requested_at=row["deletion_requested_at"], deleted_at=row["deleted_at"],
        )

    def readiness(self) -> bool:
        try:
            with self._connection() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def create_atomic(self, record: ConversionRecord, idempotency_key: str, event: dict[str, Any], outbox: dict[str, Any]) -> ConversionRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT record_id FROM submission_idempotency WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                connection.rollback()
                persisted = self.get(str(existing["record_id"]))
                if persisted is None:
                    raise RuntimeError("idempotency_record_missing")
                return persisted
            connection.execute(
                "INSERT INTO conversion_records(record_id,record_type,created_at,source_route,source_context,submitted_fields,status,retention_class) VALUES (?,?,?,?,?,?,?,?)",
                (record.record_id, record.record_type.value, record.created_at, record.source_route, self._json(record.source_context), self._json(record.submitted_fields), record.status.value, record.retention_class),
            )
            connection.execute(
                "INSERT INTO conversion_record_events(event_id,timestamp,actor_type,actor_id,record_id,action,metadata) VALUES (?,?,?,?,?,?,?)",
                (event["event_id"], event["timestamp"], event["actor_type"], event.get("actor_id"), record.record_id, event["action"], self._json(event.get("metadata", {}))),
            )
            connection.execute(
                "INSERT INTO audit_events(event_id,timestamp,actor_type,actor_id,record_id,action,safe_metadata) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), event["timestamp"], event["actor_type"], event.get("actor_id"), record.record_id, event["action"], self._json({"record_type": record.record_type.value})),
            )
            connection.execute(
                "INSERT INTO notification_outbox(outbox_id,record_id,event_type,payload,created_at) VALUES (?,?,?,?,?)",
                (outbox["outbox_id"], record.record_id, outbox["event_type"], self._json(outbox["payload"]), outbox["created_at"]),
            )
            connection.execute(
                "INSERT INTO submission_idempotency(idempotency_key,record_id,created_at) VALUES (?,?,?)",
                (idempotency_key, record.record_id, record.created_at),
            )
            connection.commit()
            return record
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_records(self) -> list[ConversionRecord]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM conversion_records WHERE deleted_at IS NULL ORDER BY created_at DESC,record_id").fetchall()
            return [self._record(row) for row in rows]

    def get(self, record_id: str) -> ConversionRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM conversion_records WHERE record_id=? AND deleted_at IS NULL", (record_id,)).fetchone()
            return self._record(row) if row else None

    def update_status(self, record_id: str, status: RecordStatus, actor_id: str, expected_status: RecordStatus | None = None) -> ConversionRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM conversion_records WHERE record_id=? AND deleted_at IS NULL", (record_id,)).fetchone()
            if row is None:
                raise KeyError("record_not_found")
            current = self._record(row)
            if expected_status is not None and current.status != expected_status:
                raise StaleStatusUpdate("stale_status_update")
            require_status_transition(current.status, status)
            if current.status != status:
                timestamp = utc_now()
                connection.execute("UPDATE conversion_records SET status=? WHERE record_id=?", (status.value, record_id))
                metadata = {"from": current.status.value, "to": status.value}
                connection.execute("INSERT INTO conversion_record_events(event_id,timestamp,actor_type,actor_id,record_id,action,metadata) VALUES (?,?,?,?,?,'status_changed',?)", (str(uuid.uuid4()), timestamp, "STAFF", actor_id, record_id, self._json(metadata)))
                connection.execute("INSERT INTO audit_events(event_id,timestamp,actor_type,actor_id,record_id,action,safe_metadata) VALUES (?,?,?,?,?,'status_changed',?)", (str(uuid.uuid4()), timestamp, "STAFF", actor_id, record_id, self._json(metadata)))
            connection.commit()
            updated = self.get(record_id)
            if updated is None:
                raise RuntimeError("updated_record_missing")
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def add_attachment(self, attachment: AttachmentRecord) -> None:
        with self._connection() as connection:
            connection.execute("INSERT INTO attachment_records VALUES (?,?,?,?,?,?,?,?,?,?)", (attachment.attachment_id, attachment.record_id, attachment.safe_storage_key, attachment.original_filename, attachment.safe_filename, attachment.mime_type, attachment.size, attachment.hash, attachment.scan_status.value, attachment.created_at))
            connection.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), utc_now(), "PUBLIC", None, attachment.record_id, "attachment_received", self._json({"attachment_id": attachment.attachment_id, "mime_type": attachment.mime_type, "size": attachment.size, "scan_status": attachment.scan_status.value})))

    def get_attachment(self, attachment_id: str) -> AttachmentRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM attachment_records WHERE attachment_id=?", (attachment_id,)).fetchone()
        if row is None:
            return None
        return AttachmentRecord(attachment_id=row["attachment_id"], record_id=row["record_id"], safe_storage_key=row["safe_storage_key"], original_filename=row["original_filename"], safe_filename=row["safe_filename"], mime_type=row["mime_type"], size=int(row["size"]), hash=row["hash"], scan_status=ScanStatus(row["scan_status"]), created_at=row["created_at"])

    def list_attachments(self, record_id: str) -> list[AttachmentRecord]:
        with self._connection() as connection:
            ids = [row[0] for row in connection.execute("SELECT attachment_id FROM attachment_records WHERE record_id=? ORDER BY created_at", (record_id,)).fetchall()]
        return [item for item in (self.get_attachment(item_id) for item_id in ids) if item is not None]


class PostgresRepository:
    """Provider-neutral DB-API adapter. Caller supplies a pooled connection."""

    CREATE_SQL = """
        INSERT INTO conversion_records
          (record_id, record_type, source_route, source_context, submitted_fields, status, retention_class)
        VALUES (%s, %s, %s, %s, %s, 'NEW', 'UNDECIDED')
        RETURNING created_at
    """

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def claim_notifications(self, *, batch_size: int = 1, lease_seconds: int = 900) -> list[dict[str, Any]]:
        if not 1 <= batch_size <= 20 or lease_seconds < 900:
            raise ValueError('invalid_notification_claim_bounds')
        owner = str(uuid.uuid4())
        with self.connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""WITH exhausted AS (
                SELECT outbox_id FROM notification_outbox
                WHERE processed_at IS NULL AND attempt_count>=8
                  AND ((delivery_status IN ('PENDING','RETRY')) OR
                       (delivery_status='PROCESSING' AND locked_at < CURRENT_TIMESTAMP - %s * INTERVAL '1 second'))
                ORDER BY created_at LIMIT 20 FOR UPDATE SKIP LOCKED
                ) UPDATE notification_outbox o SET delivery_status='DEAD',
                last_error_code='WORKER_ERROR',locked_at=NULL,locked_by=NULL
                FROM exhausted e WHERE o.outbox_id=e.outbox_id""", (lease_seconds,))
            cursor.execute("""
                WITH candidates AS (
                    SELECT outbox_id FROM notification_outbox
                    WHERE ((delivery_status IN ('PENDING','RETRY') AND next_attempt_at <= CURRENT_TIMESTAMP)
                       OR (delivery_status='PROCESSING' AND locked_at < CURRENT_TIMESTAMP - %s * INTERVAL '1 second'))
                      AND processed_at IS NULL
                      AND attempt_count < 8
                    ORDER BY next_attempt_at,created_at,outbox_id
                    LIMIT %s FOR UPDATE SKIP LOCKED
                )
                UPDATE notification_outbox o
                SET delivery_status='PROCESSING', attempt_count=o.attempt_count+1,
                    last_attempt_at=CURRENT_TIMESTAMP, locked_at=CURRENT_TIMESTAMP, locked_by=%s
                FROM candidates c WHERE o.outbox_id=c.outbox_id
                RETURNING o.outbox_id::text,o.record_id::text,o.event_type,o.attempt_count,o.locked_by
            """, (lease_seconds, batch_size, owner))
            return [dict(zip(('outbox_id','record_id','event_type','attempt_count','locked_by'), row)) for row in cursor.fetchall()]

    def finish_notification(self, item: dict[str, Any], *, error_code: str | None = None, max_attempts: int = 8) -> str:
        if error_code is not None and error_code not in SAFE_DELIVERY_ERRORS:
            raise ValueError('unsafe_notification_error_code')
        status = 'SENT' if error_code is None else ('DEAD' if item['attempt_count'] >= max_attempts else 'RETRY')
        delay = min(3600, 30 * 2 ** min(item['attempt_count'] - 1, 7))
        with self.connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE notification_outbox SET delivery_status=%s, last_error_code=%s,
                next_attempt_at=CURRENT_TIMESTAMP + %s * INTERVAL '1 second',
                processed_at=CASE WHEN %s='SENT' THEN CURRENT_TIMESTAMP ELSE processed_at END,
                locked_at=NULL,locked_by=NULL
                WHERE outbox_id=%s AND delivery_status='PROCESSING' AND locked_by=%s AND attempt_count=%s
                RETURNING delivery_status""", (status,error_code,delay,status,item['outbox_id'],item['locked_by'],item['attempt_count']))
            row = cursor.fetchone()
            if not row:
                raise RuntimeError('notification_lease_lost')
            return row[0]

    def requeue_notification(self, outbox_id: str, *, expected_attempt_count: int,
                             operator_id: str, reason_code: str) -> dict[str, Any]:
        identifier = validate_requeue(outbox_id, expected_attempt_count, operator_id, reason_code)
        with self.connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT o.record_id::text,o.delivery_status,o.attempt_count,
                o.processed_at,o.last_error_code,o.locked_by
                FROM notification_outbox o JOIN conversion_records r USING(record_id)
                WHERE o.outbox_id=%s AND r.deleted_at IS NULL FOR UPDATE OF o""", (identifier,))
            row = cursor.fetchone()
            if row is None:
                raise NotificationOperationError('OUTBOX_NOT_FOUND')
            record_id, status, attempts, processed_at, previous_error, locked_by = row
            if status != 'DEAD' or processed_at is not None or locked_by is not None:
                raise NotificationOperationError('RETRY_STATE_CONFLICT')
            if attempts != expected_attempt_count:
                raise NotificationOperationError('RETRY_ATTEMPT_CONFLICT')
            cursor.execute("""UPDATE notification_outbox SET delivery_status='PENDING',
                attempt_count=0,next_attempt_at=CURRENT_TIMESTAMP,last_error_code=NULL,
                locked_at=NULL,locked_by=NULL WHERE outbox_id=%s""", (identifier,))
            # The requeue and its audit commit together. Preserve the exhausted
            # cycle in history before granting a new bounded delivery cycle.
            self._insert_audit(cursor, record_id=record_id, actor_type='SYSTEM', actor_id=operator_id,
                action='notification_requeued', safe_metadata={
                    'outbox_id':identifier,'reason_code':reason_code,'previous_status':status,
                    'previous_attempt_count':attempts,'previous_error_code':safe_delivery_error(previous_error),
                    'new_status':'PENDING','new_attempt_count':0})
            return {'outbox_id':identifier,'record_id':record_id,'delivery_status':'PENDING',
                    'attempt_count':0,'audit_action':'notification_requeued'}

    def notification_counts(self) -> dict[str, int]:
        with self.connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT delivery_status,count(*) FROM notification_outbox GROUP BY delivery_status')
            counts = {status: 0 for status in ('PENDING','PROCESSING','RETRY','SENT','DEAD')}
            counts.update(dict(cursor.fetchall()))
            return counts

    @staticmethod
    def _hydrate_record(row) -> ConversionRecord:
        return ConversionRecord(
            record_id=str(row[0]),
            record_type=RecordType(str(row[1])),
            created_at=str(canonical_timestamp(row[2])),
            source_route=str(row[3]),
            source_context=dict(row[4]),
            submitted_fields=dict(row[5]),
            status=RecordStatus(str(row[6])),
            retention_class=str(row[7]),
            deletion_requested_at=canonical_timestamp(row[8]),
            deleted_at=canonical_timestamp(row[9]),
        )

    @staticmethod
    def _hydrate_attachment(row) -> AttachmentRecord:
        return AttachmentRecord(
            attachment_id=str(row[0]),
            record_id=str(row[1]),
            safe_storage_key=str(row[2]),
            original_filename=str(row[3]),
            safe_filename=str(row[4]),
            mime_type=str(row[5]),
            size=int(row[6]),
            hash=str(row[7]),
            scan_status=ScanStatus(str(row[8])),
            created_at=str(canonical_timestamp(row[9])),
        )

    @staticmethod
    def _insert_audit(cursor, *, record_id: str, actor_type: str, actor_id: str | None, action: str, safe_metadata: dict[str, Any]) -> None:
        cursor.execute(
            "INSERT INTO audit_events(event_id,actor_type,actor_id,record_id,action,safe_metadata) VALUES (%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), actor_type, actor_id, record_id, action, Jsonb(safe_metadata)),
        )

    def create_atomic(self, record: ConversionRecord, idempotency_key: str, event: dict[str, Any], outbox: dict[str, Any]) -> ConversionRecord:
        connection = self.connection_factory()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (idempotency_key,))
                    cursor.execute("SELECT record_id FROM submission_idempotency WHERE idempotency_key=%s", (idempotency_key,))
                    existing = cursor.fetchone()
                    if existing:
                        cursor.execute("SELECT record_id::text, record_type, created_at, source_route, source_context, submitted_fields, status, retention_class, deletion_requested_at, deleted_at FROM conversion_records WHERE record_id=%s", (existing[0],))
                        row = cursor.fetchone()
                        return self._hydrate_record(row)
                    cursor.execute(self.CREATE_SQL.replace("RETURNING created_at", "RETURNING record_id::text,record_type,created_at,source_route,source_context,submitted_fields,status,retention_class,deletion_requested_at,deleted_at"), (record.record_id, record.record_type.value, record.source_route, Jsonb(record.source_context), Jsonb(record.submitted_fields)))
                    persisted = self._hydrate_record(cursor.fetchone())
                    cursor.execute("INSERT INTO conversion_record_events(event_id, record_id, actor_type, actor_id, action, metadata) VALUES (%s,%s,%s,%s,%s,%s)", (event["event_id"], record.record_id, event["actor_type"], event.get("actor_id"), event["action"], Jsonb(event.get("metadata", {}))))
                    self._insert_audit(cursor, record_id=record.record_id, actor_type=event["actor_type"], actor_id=event.get("actor_id"), action=event["action"], safe_metadata={"record_type": record.record_type.value})
                    cursor.execute("INSERT INTO notification_outbox(outbox_id, record_id, event_type, payload) VALUES (%s,%s,%s,%s)", (outbox["outbox_id"], record.record_id, outbox["event_type"], Jsonb(outbox["payload"])))
                    cursor.execute("INSERT INTO submission_idempotency(idempotency_key, record_id) VALUES (%s,%s)", (idempotency_key, record.record_id))
            return persisted
        finally:
            connection.close()

    def list_records(self) -> list[ConversionRecord]:
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT record_id::text,record_type,created_at,source_route,source_context,submitted_fields,status,retention_class,deletion_requested_at,deleted_at FROM conversion_records WHERE deleted_at IS NULL ORDER BY created_at DESC,record_id")
                return [self._hydrate_record(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def get(self, record_id: str) -> ConversionRecord | None:
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT record_id::text,record_type,created_at,source_route,source_context,submitted_fields,status,retention_class,deletion_requested_at,deleted_at FROM conversion_records WHERE record_id=%s AND deleted_at IS NULL", (record_id,))
                row = cursor.fetchone()
                return self._hydrate_record(row) if row else None
        finally:
            connection.close()

    def update_status(self, record_id: str, status: RecordStatus, actor_id: str, expected_status: RecordStatus | None = None) -> ConversionRecord:
        connection = self.connection_factory()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT record_id::text,record_type,created_at,source_route,source_context,submitted_fields,status,retention_class,deletion_requested_at,deleted_at FROM conversion_records WHERE record_id=%s AND deleted_at IS NULL FOR UPDATE", (record_id,))
                    row = cursor.fetchone()
                    if row is None:
                        raise KeyError("record_not_found")
                    current = self._hydrate_record(row)
                    if expected_status is not None and current.status != expected_status:
                        raise StaleStatusUpdate("stale_status_update")
                    require_status_transition(current.status, status)
                    if current.status == status:
                        return current
                    cursor.execute("UPDATE conversion_records SET status=%s WHERE record_id=%s RETURNING record_id::text,record_type,created_at,source_route,source_context,submitted_fields,status,retention_class,deletion_requested_at,deleted_at", (status.value, record_id))
                    updated = self._hydrate_record(cursor.fetchone())
                    metadata = {"from": current.status.value, "to": status.value}
                    cursor.execute("INSERT INTO conversion_record_events(event_id,record_id,actor_type,actor_id,action,metadata) VALUES (%s,%s,'STAFF',%s,'status_changed',%s)", (str(uuid.uuid4()), record_id, actor_id, Jsonb(metadata)))
                    self._insert_audit(cursor, record_id=record_id, actor_type="STAFF", actor_id=actor_id, action="status_changed", safe_metadata=metadata)
                    return updated
        finally:
            connection.close()

    def add_attachment(self, attachment: AttachmentRecord) -> None:
        connection = self.connection_factory()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO attachment_records(attachment_id,record_id,safe_storage_key,original_filename,safe_filename,mime_type,size,hash,scan_status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (attachment.attachment_id, attachment.record_id, attachment.safe_storage_key, attachment.original_filename, attachment.safe_filename, attachment.mime_type, attachment.size, attachment.hash, attachment.scan_status.value, attachment.created_at))
                    self._insert_audit(cursor, record_id=attachment.record_id, actor_type="PUBLIC", actor_id=None, action="attachment_received", safe_metadata={"attachment_id": attachment.attachment_id, "mime_type": attachment.mime_type, "size": attachment.size, "scan_status": attachment.scan_status.value})
        finally:
            connection.close()

    def get_attachment(self, attachment_id: str) -> AttachmentRecord | None:
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT attachment_id::text,record_id::text,safe_storage_key,original_filename,safe_filename,mime_type,size,hash,scan_status,created_at FROM attachment_records WHERE attachment_id=%s", (attachment_id,))
                row = cursor.fetchone()
                return self._hydrate_attachment(row) if row else None
        finally:
            connection.close()

    def list_attachments(self, record_id: str) -> list[AttachmentRecord]:
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT attachment_id::text,record_id::text,safe_storage_key,original_filename,safe_filename,mime_type,size,hash,scan_status,created_at FROM attachment_records WHERE record_id=%s ORDER BY created_at,attachment_id", (record_id,))
                return [self._hydrate_attachment(row) for row in cursor.fetchall()]
        finally:
            connection.close()
