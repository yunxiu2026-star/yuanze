from __future__ import annotations

import uuid
from typing import Any

from .models import ConversionRecord, Principal, RecordStatus, RecordType, Role, utc_now
from .security import AuthorizationError, DeterministicAbuseAdapter, InMemoryRateLimiter, require
from .validation import validate_submission
from .configuration import PRODUCTION_SYNTHETIC_MARKER


class RateLimitError(RuntimeError):
    pass


class AbuseError(RuntimeError):
    pass


class ConversionService:
    def __init__(self, repository, *, rate_limiter: InMemoryRateLimiter | None = None, abuse_adapter=None, synthetic_only=False):
        self.repository = repository
        self.rate_limiter = rate_limiter or InMemoryRateLimiter()
        self.abuse_adapter = abuse_adapter or DeterministicAbuseAdapter()
        self.synthetic_only = synthetic_only

    def submit(self, record_type: RecordType, payload: object, *, idempotency_key: str, client_key: str, abuse_proof: str | None = None) -> dict[str, Any]:
        if self.synthetic_only:
            fields = payload.get('submitted_fields', {}) if isinstance(payload, dict) else {}
            context = payload.get('source_context', {}) if isinstance(payload, dict) else {}
            name = fields.get('name', fields.get('contact_name', '')) if isinstance(fields, dict) else ''
            if (not isinstance(fields, dict) or not isinstance(context, dict) or
                    context.get('source_page') != PRODUCTION_SYNTHETIC_MARKER or
                    fields.get('company') != 'B2B PRODUCTION SYNTHETIC' or
                    fields.get('work_email') != 'synthetic@example.invalid' or
                    not isinstance(name, str) or not name.startswith('PRODUCTION SYNTHETIC ')):
                raise AbuseError('production_synthetic_scope_only')
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("valid_idempotency_key_required")
        if not self.rate_limiter.allow(f"public:{client_key}"):
            raise RateLimitError("rate_limit_exceeded")
        if not self.abuse_adapter.assess(abuse_proof):
            raise AbuseError("abuse_check_failed")
        normalized = validate_submission(record_type, payload)
        record_id = str(uuid.uuid4())
        record = ConversionRecord(record_id=record_id, record_type=record_type, created_at=utc_now(), source_route=str(normalized["source_route"]), source_context=dict(normalized["source_context"]), submitted_fields=dict(normalized["submitted_fields"]))
        suffix = record_type.value
        event = {"event_id": str(uuid.uuid4()), "timestamp": utc_now(), "actor_type": "PUBLIC", "actor_id": None, "record_id": record_id, "action": "submission_created", "metadata": {"record_type": suffix}}
        outbox = {"outbox_id": str(uuid.uuid4()), "record_id": record_id, "event_type": f"{suffix}_CREATED", "payload": {"record_id": record_id}, "created_at": utc_now()}
        persisted = self.repository.create_atomic(record, idempotency_key, event, outbox)
        # Public responses are receipts, not mirrors of submitted PII.
        return {
            "inquiry_id": persisted.record_id,
            "record_id": persisted.record_id,
            "record_type": persisted.record_type.value,
            "created_at": persisted.created_at,
            "status": persisted.status.value,
            "receipt_status": "RECEIVED",
            "notification_status": "QUEUED",
        }

    def list_internal(self, principal: Principal) -> list[dict[str, Any]]:
        require(principal, "conversion:read")
        records = self.repository.list_records()
        if principal.role is not Role.ADMIN:
            allowed = principal.allowed_record_ids or frozenset()
            records = [r for r in records if r.record_id in allowed]
        return [self._internal_projection(r) for r in records]

    def get_internal(self, principal: Principal, record_id: str) -> dict[str, Any]:
        require(principal, "conversion:read", record_id)
        record = self.repository.get(record_id)
        if record is None:
            raise KeyError("record_not_found")
        return self._internal_projection(record)

    def update_status(self, principal: Principal, record_id: str, status: RecordStatus) -> dict[str, Any]:
        require(principal, "conversion:update_status", record_id)
        if status is RecordStatus.QUALIFIED:
            # Explicit staff action is accepted; never automatic from submitted fields.
            pass
        current = self.repository.get(record_id)
        if current is None:
            raise KeyError("record_not_found")
        record = self.repository.update_status(record_id, status, principal.actor_id, expected_status=current.status)
        return self._internal_projection(record)

    @staticmethod
    def sales_brief(record: ConversionRecord) -> dict[str, Any]:
        fields = record.submitted_fields
        return {
            "brief_type": "INTERNAL_DERIVED_NON_AUTHORITATIVE",
            "record_type": record.record_type.value,
            "buyer_requirements": {k: fields[k] for k in sorted(fields) if k not in {"consent"}},
            "source_context": dict(sorted(record.source_context.items())),
            "open_questions": ["Confirm requested specification", "Confirm quantity and destination", "Verify supplier capability separately"],
            "prohibited_inferences": ["B2B_CAPABILITY", "GUARANTEED_MOQ", "PRODUCTION_FEASIBILITY", "REGULATORY_ELIGIBILITY", "CERTIFICATION_AVAILABILITY", "BUYER_QUALITY_SCORE"],
        }

    def _internal_projection(self, record: ConversionRecord) -> dict[str, Any]:
        result = record.public_projection()
        result["internal_sales_brief"] = self.sales_brief(record)
        result["attachment_metadata"] = [
            {"attachment_id": a.attachment_id, "original_filename": a.original_filename, "safe_filename": a.safe_filename, "mime_type": a.mime_type, "size": a.size, "hash": a.hash, "scan_status": a.scan_status.value, "download_allowed": a.scan_status.value == "CLEAN"}
            for a in self.repository.list_attachments(record.record_id)
        ]
        return result
