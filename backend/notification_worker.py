"""Independent STAGING worker; delivery is AT_LEAST_ONCE, not exactly-once SMTP."""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
import signal
import threading

from runtime_foundation.configuration import connection_factory, postgres_readiness, require_staging, environment, require_production_chain, public_intake_enabled
from runtime_foundation.notifications import NotificationError, SMTPConfig, SMTPNotificationAdapter
from runtime_foundation.repository import PostgresRepository

LOGGER = logging.getLogger('b2b.notification')


def run_once(repository, adapter=None) -> list[dict]:
    # Configuration is deployment state, not a delivery attempt. Do not drain
    # the durable queue while the Operator has not provided SMTP credentials.
    transport = adapter if adapter is not None else SMTPNotificationAdapter(SMTPConfig.from_environment())
    results = []
    for item in repository.claim_notifications(batch_size=1):
        error = None
        try:
            record = repository.get(item['record_id'])
            if record is None:
                raise NotificationError('INVALID_NOTIFICATION')
            transport.send(record,item['outbox_id'])
        except NotificationError as exc:
            error = exc.code
        except Exception:
            error = 'WORKER_ERROR'
        status = repository.finish_notification(item,error_code=error)
        safe = {'outbox_id':item['outbox_id'],'record_id':item['record_id'],
                'delivery_status':status,'attempt_count':item['attempt_count'],'error_code':error}
        LOGGER.info(json.dumps(safe,separators=(',',':')))
        results.append(safe)
    return results


class PublicIntakeRepository(PostgresRepository):
    """Process new public inquiries; leave the pre-activation outbox untouched."""
    def __init__(self, factory, created_after):
        super().__init__(factory)
        cutoff = datetime.fromisoformat(created_after.replace('Z', '+00:00'))
        if cutoff.tzinfo is None:
            raise ValueError('notification_cutoff_timezone_required')
        self.created_after = cutoff.astimezone(timezone.utc)

    def claim_notifications(self, *, batch_size=1, lease_seconds=900):
        import uuid
        if not 1 <= batch_size <= 20 or lease_seconds < 900:
            raise ValueError('invalid_notification_claim_bounds')
        owner = str(uuid.uuid4())
        with self.connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""WITH exhausted AS (
                SELECT outbox_id FROM notification_outbox
                WHERE created_at >= %s AND processed_at IS NULL AND attempt_count>=8
                  AND ((delivery_status IN ('PENDING','RETRY')) OR
                       (delivery_status='PROCESSING' AND locked_at < CURRENT_TIMESTAMP - %s * INTERVAL '1 second'))
                ORDER BY created_at LIMIT 20 FOR UPDATE SKIP LOCKED
                ) UPDATE notification_outbox o SET delivery_status='DEAD',
                last_error_code='WORKER_ERROR',locked_at=NULL,locked_by=NULL
                FROM exhausted e WHERE o.outbox_id=e.outbox_id""", (self.created_after,lease_seconds))
            cursor.execute("""WITH candidates AS (
                SELECT outbox_id FROM notification_outbox
                WHERE created_at >= %s AND processed_at IS NULL AND attempt_count < 8
                  AND ((delivery_status IN ('PENDING','RETRY') AND next_attempt_at <= CURRENT_TIMESTAMP)
                    OR (delivery_status='PROCESSING' AND locked_at < CURRENT_TIMESTAMP - %s * INTERVAL '1 second'))
                ORDER BY next_attempt_at,created_at,outbox_id
                LIMIT %s FOR UPDATE SKIP LOCKED
                ) UPDATE notification_outbox o
                SET delivery_status='PROCESSING',attempt_count=o.attempt_count+1,
                    last_attempt_at=CURRENT_TIMESTAMP,locked_at=CURRENT_TIMESTAMP,locked_by=%s
                FROM candidates c WHERE o.outbox_id=c.outbox_id
                RETURNING o.outbox_id::text,o.record_id::text,o.event_type,o.attempt_count,o.locked_by""",
                (self.created_after,lease_seconds,batch_size,owner))
            return [dict(zip(('outbox_id','record_id','event_type','attempt_count','locked_by'),row)) for row in cursor.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--poll-seconds',type=int,default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    production = environment() == 'PRODUCTION'
    if production:
        require_production_chain()
        if not public_intake_enabled():
            raise RuntimeError('public_notification_worker_requires_public_intake')
    else:
        require_staging()
    if not postgres_readiness():
        print(json.dumps({'error_code':'POSTGRES_NOT_READY'}))
        return 1
    repository = (PublicIntakeRepository(connection_factory, os.environ['PUBLIC_INTAKE_NOTIFY_AFTER'])
                  if production else PostgresRepository(connection_factory))
    if args.status:
        print(json.dumps({'delivery_semantics':'AT_LEAST_ONCE','counts':repository.notification_counts()}))
        return 0
    stopped = threading.Event()
    configuration_error = None
    for signum in (signal.SIGINT,signal.SIGTERM):
        signal.signal(signum,lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            run_once(repository)
            configuration_error = None
        except NotificationError as exc:
            if configuration_error != exc.code:
                LOGGER.warning(json.dumps({'error_code':exc.code,'worker_state':'WAITING_CONFIGURATION'}))
            configuration_error = exc.code
            if args.once:
                return 2
        except Exception:
            LOGGER.error(json.dumps({'error_code':'WORKER_DATABASE_ERROR'}))
            if args.once:
                return 1
        if args.once:
            break
        stopped.wait(max(1,args.poll_seconds))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
