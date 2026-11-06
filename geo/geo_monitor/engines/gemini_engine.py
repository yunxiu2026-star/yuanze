"""Gemini via generateContent with Grounding with Google Search."""
from __future__ import annotations

import logging
import re

import httpx

from .base import Citation, Engine, EngineResult

log = logging.getLogger(__name__)
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.I)


class GeminiEngine(Engine):
    name = "gemini"

    @property
    def model(self) -> str:
        return self.cfg.gemini_model

    def ask(self, prompt: str, country: str | None = None) -> EngineResult:
        body = {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
        data = self.post_json(
            URL.format(model=self.model),
            {"x-goog-api-key": self.cfg.gemini_api_key, "Content-Type": "application/json"},
            body,
        )
        result = parse_response(data, self.model)
        if self.cfg.gemini_resolve_redirects:
            self._resolve(result.citations)
        return result

    def _resolve(self, citations: list[Citation]) -> None:
        """Gemini cites through vertexaisearch redirect links; the title is
        usually the bare domain. Follow the redirect only where we need the
        real path: our own domain (to check which page was cited) or when the
        title is not a domain."""
        target = self.cfg.target_domain
        for c in citations:
            title_dom = c.title.lower().removeprefix("www.")
            is_domain = bool(DOMAIN_RE.match(title_dom))
            if is_domain and not (title_dom == target or title_dom.endswith("." + target)):
                continue
            if "vertexaisearch" not in c.url:
                continue
            try:
                resp = self.client.get(c.url, follow_redirects=False, timeout=15)
                loc = resp.headers.get("location")
                if loc:
                    c.url = loc
            except httpx.HTTPError as exc:
                log.debug("无法解析 Gemini 跳转链接 %s: %s", c.url, exc)


def parse_response(data: dict, model: str = "") -> EngineResult:
    cands = data.get("candidates") or []
    cand = cands[0] if cands else {}
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
    meta = cand.get("groundingMetadata") or {}

    chunks: list[Citation] = []
    for ch in meta.get("groundingChunks") or []:
        web = ch.get("web") or {}
        if web.get("uri"):
            title = web.get("title") or ""
            dom = title.lower().removeprefix("www.") if DOMAIN_RE.match(title) else ""
            chunks.append(Citation(url=web["uri"], title=title, domain=dom))

    # Order chunks by where they are first used in the answer.
    first_use: dict[int, int] = {}
    for sup in meta.get("groundingSupports") or []:
        start = int((sup.get("segment") or {}).get("startIndex") or 0)
        for i in sup.get("groundingChunkIndices") or []:
            first_use[i] = min(first_use.get(i, start), start)
    order = sorted(range(len(chunks)), key=lambda i: (first_use.get(i, 10**9), i))
    cited = [chunks[i] for i in order]

    usage = data.get("usageMetadata") or {}
    queries = [q for q in meta.get("webSearchQueries") or [] if q]
    return EngineResult(
        engine="gemini", model=data.get("modelVersion") or model, text=text,
        citations=cited, sources=[], search_queries=queries,
        input_tokens=int(usage.get("promptTokenCount") or 0),
        output_tokens=int(usage.get("candidatesTokenCount") or 0)
        + int(usage.get("thoughtsTokenCount") or 0),
        search_calls=1 if meta else 0,  # billed per grounded request
    )
