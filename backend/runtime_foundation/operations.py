"""ID-only Operator inspection. No public endpoint or buyer payload projection."""
from __future__ import annotations

import re
import uuid

from .notifications import SAFE_DELIVERY_ERRORS

REQUEUE_REASONS = frozenset({'SMTP_CONFIGURATION_FIXED', 'SMTP_SERVICE_RECOVERED', 'WORKER_RECOVERED'})


class NotificationOperationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical_id(value, error_code):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise NotificationOperationError(error_code) from None


def validate_requeue(outbox_id, expected_attempt_count, operator_id, reason_code):
    identifier = canonical_id(outbox_id, 'INVALID_RETRY_REQUEST')
    if (type(expected_attempt_count) is not int or not 0 <= expected_attempt_count <= 1_000_000
            or not isinstance(operator_id, str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', operator_id)
            or not isinstance(reason_code, str)
            or reason_code not in REQUEUE_REASONS):
        raise NotificationOperationError('INVALID_RETRY_REQUEST')
    return identifier


def safe_delivery_error(value):
    return value if value is None or value in SAFE_DELIVERY_ERRORS else 'UNKNOWN_DELIVERY_ERROR'


def inspect_inquiries(connection_factory, *, record_id=None, limit=20):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationOperationError('INVALID_INSPECTION_REQUEST')
    identifier = canonical_id(record_id, 'INVALID_INSPECTION_REQUEST') if record_id is not None else None
    columns = ('record_id','record_type','created_at','status','outbox_id','delivery_status',
               'attempt_count','last_error_code','last_attempt_at','next_attempt_at','processed_at',
               'locked_at','event_count','audit_count','idempotency_count')
    with connection_factory() as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT r.record_id::text,r.record_type,r.created_at,r.status,
            o.outbox_id::text,o.delivery_status,o.attempt_count,o.last_error_code,
            o.last_attempt_at,o.next_attempt_at,o.processed_at,o.locked_at,
            (SELECT count(*) FROM conversion_record_events e WHERE e.record_id=r.record_id),
            (SELECT count(*) FROM audit_events a WHERE a.record_id=r.record_id),
            (SELECT count(*) FROM submission_idempotency i WHERE i.record_id=r.record_id)
            FROM conversion_records r LEFT JOIN notification_outbox o USING(record_id)
            WHERE r.deleted_at IS NULL AND (%s::uuid IS NULL OR r.record_id=%s::uuid)
            ORDER BY r.created_at DESC,r.record_id,o.created_at,o.outbox_id LIMIT %s""",
            (identifier, identifier, limit))
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for record in records:
        record['last_error_code'] = safe_delivery_error(record['last_error_code'])
    return records
