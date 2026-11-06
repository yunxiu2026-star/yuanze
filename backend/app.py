"""Public intake WSGI. STAGING staff access and Production activation are disabled."""
from __future__ import annotations

import json
import ipaddress
import logging
import os
import re
import uuid
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

from runtime_foundation.api import RuntimeAPI
from runtime_foundation.configuration import allowed_origins, connection_factory, environment, postgres_readiness, required as _required, require_production_chain, public_intake_enabled
from runtime_foundation.models import Principal, Role
from runtime_foundation.repository import PostgresRepository, SqliteRepository
from runtime_foundation.security import DisabledInternalAuthAdapter, InMemoryRateLimiter, LocalTestAuthAdapter, OriginPolicy, SECURITY_HEADERS
from runtime_foundation.service import ConversionService

MAX_BODY_BYTES = 64 * 1024
LOGGER = logging.getLogger("b2b.intake")
if not LOGGER.handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")


def build_api() -> RuntimeAPI:
    current_environment = environment()
    if current_environment == "PRODUCTION":
        require_production_chain()
    if current_environment == "LOCAL":
        token = _required("LOCAL_REVIEWER_TOKEN")
        auth = LocalTestAuthAdapter({token: Principal("offline-reviewer", Role.ADMIN)})
    else:
        auth = DisabledInternalAuthAdapter()
    origins = allowed_origins()
    persistence = os.getenv("PERSISTENCE_BACKEND", "sqlite").lower()
    if current_environment == "STAGING" and persistence != "postgres":
        raise RuntimeError("staging_postgres_required")
    if persistence == "sqlite":
        default_path = Path(__file__).resolve().parent / "local-data" / "product-brief-v1.sqlite3"
        repository = SqliteRepository(os.getenv("SQLITE_DATABASE_PATH", str(default_path)))
        readiness_check = repository.readiness
    elif persistence == "postgres":
        repository = PostgresRepository(connection_factory)
        readiness_check = postgres_readiness
        if not readiness_check():
            raise RuntimeError("postgres_version_or_schema_not_ready")
    else:
        raise RuntimeError("unsupported_persistence_backend")
    limiter = InMemoryRateLimiter(
        limit=int(os.getenv("PUBLIC_RATE_LIMIT", "5")),
        window_seconds=int(os.getenv("PUBLIC_RATE_WINDOW_SECONDS", "60")),
    )
    return RuntimeAPI(ConversionService(repository, rate_limiter=limiter, synthetic_only=current_environment == 'PRODUCTION' and not public_intake_enabled()), auth, OriginPolicy(origins), readiness_check)


API = build_api()


def _safe_route(path):
    allowed = {'/api/v1/product-brief','/api/v1/sample-requests','/api/v1/rfqs','/api/v1/health','/api/v1/readiness'}
    return path if path in allowed else '/api/v1/internal/*' if path.startswith('/api/v1/internal/') else 'UNMATCHED_ROUTE'


def _direct_error(environ, start_response, status: int, code: str):
    request_id = str(uuid.uuid4())
    headers = {"Content-Type": "application/json; charset=utf-8", **SECURITY_HEADERS, "X-Request-ID": request_id}
    origin = environ.get("HTTP_ORIGIN")
    if origin and API.origins.allow(origin, False):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    payload = {"error_code": code, "message": "Request could not be completed.", "request_id": request_id}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers["Content-Length"] = str(len(raw))
    start_response(f"{status} {_reason(status)}", list(headers.items()))
    LOGGER.warning(json.dumps({"request_id": request_id, "path": _safe_route(environ.get("PATH_INFO", "/")), "status": status, "result": code}, separators=(",", ":")))
    return [raw]


def _client_key(environ):
    remote = environ.get('REMOTE_ADDR', 'unknown')
    trusted = {value.strip() for value in os.getenv('TRUSTED_PROXY_IPS', '').split(',') if value.strip()}
    if remote in trusted:
        forwarded = environ.get('HTTP_X_FORWARDED_FOR', '').strip()
        try:
            # Nginx overwrites this header with one client address. Reject chains.
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return remote


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")
    origin = environ.get("HTTP_ORIGIN")
    base_headers = {
        "origin": origin or "",
        "authorization": environ.get("HTTP_AUTHORIZATION", ""),
        "idempotency-key": environ.get("HTTP_IDEMPOTENCY_KEY", ""),
        "x-abuse-proof": environ.get("HTTP_X_ABUSE_PROOF", ""),
    }
    if path.startswith('/api/v1/internal/') and isinstance(API.auth, DisabledInternalAuthAdapter):
        return _direct_error(environ,start_response,403,'internal_access_disabled')
    if method == "OPTIONS":
        if origin and API.origins.allow(origin, path.startswith("/api/v1/internal/")):
            headers = [("Access-Control-Allow-Origin", origin), ("Vary", "Origin"),
                       ("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS"),
                       ("Access-Control-Allow-Headers", "Content-Type, Idempotency-Key, Authorization, X-Abuse-Proof"),
                       ("Access-Control-Max-Age", "600")]
            start_response("204 No Content", headers)
            return [b""]
        start_response("403 Forbidden", [("Content-Type", "application/json")])
        return [b'{"error_code":"origin_not_allowed"}']
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return _direct_error(environ, start_response, 400, "invalid_content_length")
    if length < 0:
        return _direct_error(environ, start_response, 400, "invalid_content_length")
    if length > MAX_BODY_BYTES:
        return _direct_error(environ, start_response, 413, "request_too_large")
    media_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
    if method in {"POST", "PATCH"} and length and media_type != "application/json":
        return _direct_error(environ, start_response, 415, "json_content_type_required")
    try:
        body = json.loads(environ["wsgi.input"].read(length) or b"null") if length else None
    except (ValueError, UnicodeDecodeError):
        return _direct_error(environ, start_response, 400, "invalid_json")
    status, headers, payload = API.handle(method, path, body, base_headers, _client_key(environ))
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers["Content-Length"] = str(len(raw))
    start_response(f"{status} {_reason(status)}", list(headers.items()))
    LOGGER.info(json.dumps({"request_id": headers.get("X-Request-ID"), "record_id": payload.get("record_id"), "path": _safe_route(path), "method": method, "status": status, "result": payload.get("error_code", payload.get("receipt_status", "OK"))}, separators=(",", ":")))
    return [raw]


def _reason(status: int) -> str:
    return {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 403: "Forbidden",
            404: "Not Found", 413: "Payload Too Large", 415: "Unsupported Media Type", 422: "Unprocessable Entity", 429: "Too Many Requests",
            500: "Internal Server Error", 503: "Service Unavailable"}.get(status, "Response")


if __name__ == "__main__":
    host = os.getenv("BIND_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8780"))
    class SafeRequestHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            # Structured application logger already records route/status. Never
            # echo an HTTP request line (which may contain a buyer's query string).
            pass
    with make_server(host, port, application, handler_class=SafeRequestHandler) as server:
        server.serve_forever()
