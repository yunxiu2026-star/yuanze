BEGIN;

CREATE TABLE IF NOT EXISTS conversion_records (
  record_id UUID PRIMARY KEY,
  record_type TEXT NOT NULL CHECK (record_type IN ('PRODUCT_BRIEF','SAMPLE_REQUEST','RFQ')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_route TEXT NOT NULL,
  source_context JSONB NOT NULL CHECK (jsonb_typeof(source_context) = 'object'),
  submitted_fields JSONB NOT NULL CHECK (jsonb_typeof(submitted_fields) = 'object'),
  status TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','IN_REVIEW','NEEDS_INFO','QUALIFIED','CLOSED')),
  retention_class TEXT NOT NULL DEFAULT 'UNDECIDED',
  deletion_requested_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS conversion_record_events (
  event_id UUID PRIMARY KEY,
  timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('PUBLIC','STAFF','SYSTEM')),
  actor_id TEXT,
  record_id UUID NOT NULL REFERENCES conversion_records(record_id),
  action TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS attachment_records (
  attachment_id UUID PRIMARY KEY,
  record_id UUID NOT NULL REFERENCES conversion_records(record_id),
  safe_storage_key TEXT NOT NULL UNIQUE,
  original_filename TEXT NOT NULL,
  safe_filename TEXT NOT NULL,
  mime_type TEXT NOT NULL CHECK (mime_type IN ('application/pdf','image/png','image/jpeg','image/webp')),
  size BIGINT NOT NULL CHECK (size > 0 AND size <= 5242880),
  hash CHAR(64) NOT NULL,
  scan_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (scan_status IN ('PENDING','CLEAN','REJECTED','ERROR')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notification_outbox (
  outbox_id UUID PRIMARY KEY,
  record_id UUID NOT NULL REFERENCES conversion_records(record_id),
  event_type TEXT NOT NULL CHECK (event_type IN ('PRODUCT_BRIEF_CREATED','SAMPLE_REQUEST_CREATED','RFQ_CREATED')),
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audit_events (
  event_id UUID PRIMARY KEY,
  timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  record_id UUID REFERENCES conversion_records(record_id),
  action TEXT NOT NULL,
  safe_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS submission_idempotency (
  idempotency_key TEXT PRIMARY KEY CHECK (length(idempotency_key) BETWEEN 1 AND 128),
  record_id UUID NOT NULL UNIQUE REFERENCES conversion_records(record_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversion_records_created_at ON conversion_records(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversion_events_record_id ON conversion_record_events(record_id);
CREATE INDEX IF NOT EXISTS idx_attachments_record_id ON attachment_records(record_id);
CREATE INDEX IF NOT EXISTS idx_outbox_unprocessed ON notification_outbox(created_at) WHERE processed_at IS NULL;

COMMIT;
