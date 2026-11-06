BEGIN;

ALTER TABLE notification_outbox ADD COLUMN IF NOT EXISTS delivery_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE notification_outbox ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE notification_outbox ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;
ALTER TABLE notification_outbox ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE notification_outbox ADD COLUMN IF NOT EXISTS last_error_code TEXT;
ALTER TABLE notification_outbox ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
ALTER TABLE notification_outbox ADD COLUMN IF NOT EXISTS locked_by TEXT;
UPDATE notification_outbox SET delivery_status='SENT' WHERE processed_at IS NOT NULL AND delivery_status='PENDING';
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='notification_outbox'::regclass AND conname='outbox_delivery_status_check') THEN
    ALTER TABLE notification_outbox ADD CONSTRAINT outbox_delivery_status_check CHECK (delivery_status IN ('PENDING','PROCESSING','RETRY','SENT','DEAD'));
    ALTER TABLE notification_outbox ADD CONSTRAINT outbox_attempt_count_check CHECK (attempt_count >= 0);
    ALTER TABLE notification_outbox ADD CONSTRAINT outbox_sent_processed_check CHECK (delivery_status != 'SENT' OR processed_at IS NOT NULL);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_outbox_delivery_due ON notification_outbox(next_attempt_at,created_at) WHERE delivery_status IN ('PENDING','RETRY');
CREATE INDEX IF NOT EXISTS idx_outbox_processing_lease ON notification_outbox(locked_at) WHERE delivery_status='PROCESSING';
COMMENT ON TABLE notification_outbox IS 'Durable at-least-once notification delivery. SMTP acceptance is not inbox delivery or buyer/capability verification.';

COMMIT;
