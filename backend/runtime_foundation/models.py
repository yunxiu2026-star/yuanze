from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RecordType(StrEnum):
    PRODUCT_BRIEF = "PRODUCT_BRIEF"
    SAMPLE_REQUEST = "SAMPLE_REQUEST"
    RFQ = "RFQ"


class RecordStatus(StrEnum):
    NEW = "NEW"
    IN_REVIEW = "IN_REVIEW"
    NEEDS_INFO = "NEEDS_INFO"
    QUALIFIED = "QUALIFIED"
    CLOSED = "CLOSED"


ALLOWED_STATUS_TRANSITIONS = {
    RecordStatus.NEW: frozenset({RecordStatus.IN_REVIEW, RecordStatus.NEEDS_INFO, RecordStatus.CLOSED}),
    RecordStatus.IN_REVIEW: frozenset({RecordStatus.NEEDS_INFO, RecordStatus.QUALIFIED, RecordStatus.CLOSED}),
    RecordStatus.NEEDS_INFO: frozenset({RecordStatus.IN_REVIEW, RecordStatus.CLOSED}),
    RecordStatus.QUALIFIED: frozenset({RecordStatus.IN_REVIEW, RecordStatus.CLOSED}),
    RecordStatus.CLOSED: frozenset(),
}


class InvalidStatusTransition(ValueError):
    pass


class StaleStatusUpdate(RuntimeError):
    pass


def require_status_transition(current: RecordStatus, target: RecordStatus) -> None:
    """Canonical lifecycle authority shared by every repository."""
    if current == target:
        return
    if target not in ALLOWED_STATUS_TRANSITIONS[current]:
        raise InvalidStatusTransition(f"invalid_status_transition:{current.value}:{target.value}")


class ScanStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class Role(StrEnum):
    PUBLIC_SUBMITTER = "PUBLIC_SUBMITTER"
    SALES_REVIEWER = "SALES_REVIEWER"
    ADMIN = "ADMIN"


ROLE_CAPABILITIES = {
    Role.PUBLIC_SUBMITTER: frozenset({"conversion:submit"}),
    Role.SALES_REVIEWER: frozenset({"conversion:read", "conversion:update_status", "attachment:read"}),
    Role.ADMIN: frozenset({"conversion:read", "conversion:update_status", "attachment:read", "audit:read"}),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_timestamp(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role: Role
    allowed_record_ids: frozenset[str] | None = None


@dataclass
class ConversionRecord:
    record_id: str
    record_type: RecordType
    created_at: str
    source_route: str
    source_context: dict[str, Any]
    submitted_fields: dict[str, Any]
    status: RecordStatus = RecordStatus.NEW
    retention_class: str = "UNDECIDED"
    deletion_requested_at: str | None = None
    deleted_at: str | None = None

    def public_projection(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_type": self.record_type.value,
            "created_at": self.created_at,
            "source_route": self.source_route,
            "source_context": dict(self.source_context),
            "submitted_fields": dict(self.submitted_fields),
            "status": self.status.value,
        }


@dataclass
class AttachmentRecord:
    attachment_id: str
    record_id: str
    safe_storage_key: str
    original_filename: str
    safe_filename: str
    mime_type: str
    size: int
    hash: str
    scan_status: ScanStatus
    created_at: str = field(default_factory=utc_now)
