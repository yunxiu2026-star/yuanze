from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .models import AttachmentRecord, ScanStatus, utc_now

MAX_UPLOAD = 5 * 1024 * 1024
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
SIGNATURES = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}
EXTENSIONS = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class AttachmentError(ValueError):
    pass


class FakeScanner:
    def scan(self, content: bytes) -> ScanStatus:
        return ScanStatus.REJECTED if b"TEST-MALWARE" in content else ScanStatus.CLEAN


@dataclass
class IsolatedStorage:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, content: bytes) -> None:
        target = (self.root / key).resolve()
        if self.root.resolve() not in target.parents:
            raise AttachmentError("unsafe_storage_key")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def validate_attachment(record_id: str, filename: str, declared_mime: str, content: bytes, scanner: FakeScanner) -> AttachmentRecord:
    if not content or len(content) > MAX_UPLOAD:
        raise AttachmentError("invalid_size")
    original = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    suffix = Path(original).suffix.lower()
    expected = EXTENSIONS.get(suffix)
    if expected is None or expected != declared_mime:
        raise AttachmentError("unsupported_or_spoofed_type")
    signatures = SIGNATURES[declared_mime]
    if declared_mime == "image/webp":
        valid = content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    else:
        valid = any(content.startswith(sig) for sig in signatures)
    if not valid:
        raise AttachmentError("magic_byte_mismatch")
    safe = SAFE_NAME.sub("_", original).strip("._") or "upload"
    attachment_id = str(uuid.uuid4())
    scan_status = scanner.scan(content)
    return AttachmentRecord(
        attachment_id=attachment_id,
        record_id=record_id,
        safe_storage_key=f"quarantine/{attachment_id}",
        original_filename=original,
        safe_filename=safe,
        mime_type=declared_mime,
        size=len(content),
        hash=hashlib.sha256(content).hexdigest(),
        scan_status=scan_status,
        created_at=utc_now(),
    )
