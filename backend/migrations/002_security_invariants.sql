BEGIN;

COMMENT ON TABLE conversion_records IS 'Buyer submissions only; never a Demo Works Claim Registry authority.';
COMMENT ON COLUMN conversion_records.source_context IS 'CLIENT_PROVIDED_CONTEXT unless separately server-attested in a future authorized phase.';
COMMENT ON COLUMN conversion_records.retention_class IS 'Structural placeholder; final legal retention policy requires Human/legal approval.';
COMMENT ON TABLE notification_outbox IS 'Persistent events only; no provider or real email sender is configured.';

CREATE OR REPLACE FUNCTION prevent_event_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'conversion_record_events are append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS conversion_events_append_only ON conversion_record_events;
CREATE TRIGGER conversion_events_append_only
BEFORE UPDATE OR DELETE ON conversion_record_events
FOR EACH ROW EXECUTE FUNCTION prevent_event_mutation();

COMMIT;
