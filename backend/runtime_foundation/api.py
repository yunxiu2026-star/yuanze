from __future__ import annotations

import json
import traceback
import uuid
from typing import Any

from .models import RecordStatus, RecordType
from .security import AuthenticationError, AuthorizationError, OriginPolicy, SECURITY_HEADERS
from .service import AbuseError, RateLimitError
from .validation import ValidationError

ROUTES = {
    "/api/v1/product-brief": RecordType.PRODUCT_BRIEF,
    "/api/v1/sample-requests": RecordType.SAMPLE_REQUEST,
    "/api/v1/rfqs": RecordType.RFQ,
}


class RuntimeAPI:
    def __init__(self, service, auth_adapter, origin_policy: OriginPolicy, readiness_check=lambda: True):
        self.service = service
        self.auth = auth_adapter
        self.origins = origin_policy
        self.readiness_check = readiness_check

    def handle(self, method: str, path: str, body: object | None = None, headers: dict[str, str] | None = None, client_key: str = "local-test") -> tuple[int, dict[str, str], dict[str, Any]]:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        request_id = str(uuid.uuid4())
        response_headers = {"Content-Type": "application/json; charset=utf-8", **SECURITY_HEADERS, "X-Request-ID": request_id}
        origin = headers.get("origin")
        if not self.origins.allow(origin, path.startswith("/api/v1/internal/")):
            return self._error(403, "origin_not_allowed", request_id, response_headers)
        if origin:
            response_headers["Access-Control-Allow-Origin"] = origin
            response_headers["Vary"] = "Origin"
        try:
            if method == "GET" and path == "/api/v1/health":
                return 200, response_headers, {"status": "alive"}
            if method == "GET" and path == "/api/v1/readiness":
                ready = bool(self.readiness_check())
                return (200 if ready else 503), response_headers, {"status": "ready" if ready else "not_ready"}
            if method == "POST" and path in ROUTES:
                result = self.service.submit(ROUTES[path], body, idempotency_key=headers.get("idempotency-key", ""), client_key=client_key, abuse_proof=headers.get("x-abuse-proof"))
                return 201, response_headers, result
            if path == "/api/v1/internal/conversions" and method == "GET":
                principal = self._principal(headers)
                return 200, response_headers, {"records": self.service.list_internal(principal)}
            prefix = "/api/v1/internal/conversions/"
            if path.startswith(prefix):
                remainder = path[len(prefix):]
                principal = self._principal(headers)
                if remainder.endswith("/status") and method == "PATCH":
                    record_id = remainder[:-7].rstrip("/")
                    return 200, response_headers, self.service.update_status(principal, record_id, RecordStatus(str((body or {}).get("status"))))
                if method == "GET":
                    return 200, response_headers, self.service.get_internal(principal, remainder)
            return self._error(404, "not_found", request_id, response_headers)
        except ValidationError as exc:
            return self._error(422, "validation_failed", request_id, response_headers, exc.field_errors)
        except (AuthenticationError, AuthorizationError) as exc:
            return self._error(403, str(exc), request_id, response_headers)
        except RateLimitError:
            return self._error(429, "rate_limit_exceeded", request_id, response_headers)
        except AbuseError:
            return self._error(403, "abuse_check_failed", request_id, response_headers)
        except (KeyError, ValueError):
            return self._error(400, "invalid_request", request_id, response_headers)
        except Exception:
            # Diagnostics belong in an injected server logger, never the client response.
            return self._error(500, "internal_error", request_id, response_headers)

    def _principal(self, headers):
        raw = headers.get("authorization", "")
        return self.auth.authenticate(raw[7:] if raw.startswith("Bearer ") else None)

    @staticmethod
    def _error(status: int, code: str, request_id: str, headers: dict[str, str], field_errors: dict[str, str] | None = None):
        body: dict[str, Any] = {"error_code": code, "message": "Request could not be completed.", "request_id": request_id}
        if field_errors:
            body["field_errors"] = field_errors
        return status, headers, body
