"""Perplexity via the Agent API (POST /v1/agent) with web search.

The documented response shape has varied, so the parser accepts both the
Responses-style `output` array and older top-level `citations` /
`search_results` fields.
"""
from __future__ import annotations

import re

from .base import Citation, Engine, EngineResult

URL = "https://api.perplexity.ai/v1/agent"
MARKER_RE = re.compile(r"\[(\d+)\]")


class PerplexityEngine(Engine):
    name = "perplexity"

    @property
    def model(self) -> str:
        return self.cfg.perplexity_model or f"preset:{self.cfg.perplexity_preset}"

    def build_body(self, prompt: str, country: str | None) -> dict:
        if self.cfg.perplexity_model:
            tool: dict = {"type": "web_search"}
            if country:
                tool["user_location"] = {"country": country}
            return {"model": self.cfg.perplexity_model, "input": prompt, "tools": [tool]}
        return {"preset": self.cfg.perplexity_preset or "fast", "input": prompt}

    def ask(self, prompt: str, country: str | None = None) -> EngineResult:
        data = self.post_json(
            URL,
            {"Authorization": f"Bearer {self.cfg.perplexity_api_key}", "Content-Type": "application/json"},
            self.build_body(prompt, country),
        )
        return parse_response(data, self.model)


def _cit(obj) -> Citation | None:
    if isinstance(obj, str):
        return Citation(url=obj)
    if isinstance(obj, dict) and obj.get("url"):
        return Citation(url=obj["url"], title=obj.get("title") or "")
    return None


def parse_response(data: dict, model: str = "") -> EngineResult:
    texts: list[str] = []
    annotated: list[Citation] = []
    results: list[Citation] = []
    queries: list[str] = []
    search_items = 0

    for item in data.get("output") or []:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") in ("output_text", "text"):
                    texts.append(part.get("text") or "")
                    for ann in part.get("annotations") or []:
                        c = _cit(ann)
                        if c:
                            annotated.append(c)
        elif kind in ("search_results", "web_search_call"):
            search_items += 1
            queries.extend(q for q in item.get("queries") or [] if isinstance(q, str))
            for r in item.get("results") or []:
                c = _cit(r)
                if c:
                    results.append(c)

    # Fallbacks for other documented shapes.
    if not texts and isinstance(data.get("output_text"), str):
        texts.append(data["output_text"])
    if not texts:
        for ch in data.get("choices") or []:
            msg = ch.get("message") or {}
            if msg.get("content"):
                texts.append(msg["content"])
    for key in ("citations", "search_results", "results"):
        for r in data.get(key) or []:
            c = _cit(r)
            if c:
                results.append(c)

    text = "\n\n".join(texts)
    # If the answer carries [n] markers, order cited results by first marker.
    cited: list[Citation] = list(annotated)
    if not cited and results:
        nums: list[int] = []
        for m in MARKER_RE.finditer(text):
            n = int(m.group(1))
            if 1 <= n <= len(results) and n not in nums:
                nums.append(n)
        cited = [results[n - 1] for n in nums] if nums else list(results)

    usage = data.get("usage") or {}
    details = (usage.get("tool_calls_details") or {}).get("search_web") or {}
    calls = details.get("invocation") or details.get("invocations")
    return EngineResult(
        engine="perplexity", model=data.get("model") or model, text=text,
        citations=cited, sources=results, search_queries=queries,
        input_tokens=int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        search_calls=int(calls) if calls else search_items,
    )
