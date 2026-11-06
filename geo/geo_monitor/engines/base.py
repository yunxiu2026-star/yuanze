"""Shared engine types and HTTP helpers."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class EngineError(Exception):
    pass


@dataclass
class Citation:
    url: str
    title: str = ""
    domain: str = ""  # filled in when the URL alone does not reveal it (Gemini redirects)


@dataclass
class EngineResult:
    engine: str
    model: str
    text: str
    citations: list[Citation] = field(default_factory=list)  # cited in the answer, in order
    sources: list[Citation] = field(default_factory=list)    # consulted but not necessarily cited
    search_queries: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    search_calls: int = 0


class Engine:
    name = "base"

    def __init__(self, cfg, client: httpx.Client | None = None):
        self.cfg = cfg
        self.client = client or httpx.Client(timeout=cfg.request_timeout)

    @property
    def model(self) -> str:  # pragma: no cover - overridden
        return ""

    def ask(self, prompt: str, country: str | None = None) -> EngineResult:  # pragma: no cover
        raise NotImplementedError

    def post_json(self, url: str, headers: dict, body: dict, attempts: int = 4) -> dict[str, Any]:
        delay = 3.0
        last_err = ""
        for attempt in range(1, attempts + 1):
            try:
                resp = self.client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code < 400:
                    return resp.json()
                last_err = f"HTTP {resp.status_code}: {resp.text[:500]}"
                if resp.status_code not in RETRY_STATUS:
                    raise EngineError(last_err)
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            if attempt < attempts:
                log.warning("%s 请求失败（第 %d 次），%.0f 秒后重试: %s", self.name, attempt, delay, last_err)
                time.sleep(delay)
                delay *= 3
        raise EngineError(last_err)
