from __future__ import annotations

import hashlib
import hmac
import uuid

from .storage import FakeScanner, IsolatedStorage, validate_attachment


class UploadTicketAdapter:
    """Local/test ticket seam. The signing key is injected, never committed."""

    def __init__(self, signing_key: bytes):
        if len(signing_key) < 16:
            raise ValueError("test_signing_key_too_short")
        self.signing_key = signing_key

    def issue(self, record_id: str) -> str:
        signature = hmac.new(self.signing_key, record_id.encode(), hashlib.sha256).hexdigest()
        return f"{record_id}.{signature}"

    def verify(self, record_id: str, ticket: str) -> bool:
        return hmac.compare_digest(self.issue(record_id), ticket)


class AttachmentIntake:
    def __init__(self, repository, storage: IsolatedStorage, scanner: FakeScanner, tickets: UploadTicketAdapter):
        self.repository = repository
        self.storage = storage
        self.scanner = scanner
        self.tickets = tickets

    def receive(self, record_id: str, ticket: str, filename: str, declared_mime: str, content: bytes):
        if self.repository.get(record_id) is None or not self.tickets.verify(record_id, ticket):
            raise PermissionError("invalid_upload_authorization")
        attachment = validate_attachment(record_id, filename, declared_mime, content, self.scanner)
        self.storage.save(attachment.safe_storage_key, content)
        self.repository.add_attachment(attachment)
        return {"attachment_id": attachment.attachment_id, "safe_filename": attachment.safe_filename, "mime_type": attachment.mime_type, "size": attachment.size, "hash": attachment.hash, "scan_status": attachment.scan_status.value}
