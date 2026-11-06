"""ChatGPT via the OpenAI Responses API with the web_search tool."""
from __future__ import annotations

from .base import Citation, Engine, EngineResult

URL = "https://api.openai.com/v1/responses"


class OpenAIEngine(Engine):
    name = "openai"

    @property
    def model(self) -> str:
        return self.cfg.openai_model

    def build_body(self, prompt: str, country: str | None) -> dict:
        tool: dict = {"type": "web_search"}
        if self.cfg.openai_search_context:
            tool["search_context_size"] = self.cfg.openai_search_context
        if country:
            tool["user_location"] = {"type": "approximate", "country": country}
        body: dict = {
            "model": self.model,
            "input": prompt,
            "tools": [tool],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
        }
        if self.cfg.openai_reasoning_effort:
            body["reasoning"] = {"effort": self.cfg.openai_reasoning_effort}
        return body

    def ask(self, prompt: str, country: str | None = None) -> EngineResult:
        data = self.post_json(
            URL,
            {"Authorization": f"Bearer {self.cfg.openai_api_key}", "Content-Type": "application/json"},
            self.build_body(prompt, country),
        )
        return parse_response(data, self.model)


def parse_response(data: dict, model: str = "") -> EngineResult:
    texts: list[str] = []
    cites: list[tuple[int, Citation]] = []
    sources: list[Citation] = []
    queries: list[str] = []
    search_calls = 0
    offset = 0

    for item in data.get("output") or []:
        kind = item.get("type")
        if kind == "web_search_call":
            search_calls += 1
            action = item.get("action") or {}
            if action.get("query"):
                queries.append(action["query"])
            queries.extend(q for q in action.get("queries") or [] if q)
            for src in action.get("sources") or []:
                if src.get("url"):
                    sources.append(Citation(url=src["url"], title=src.get("title", "")))
        elif kind == "message":
            for part in item.get("content") or []:
                if part.get("type") != "output_text":
                    continue
                text = part.get("text") or ""
                for ann in part.get("annotations") or []:
                    if ann.get("type") == "url_citation" and ann.get("url"):
                        cites.append((offset + int(ann.get("start_index") or 0),
                                      Citation(url=ann["url"], title=ann.get("title", ""))))
                texts.append(text)
                offset += len(text) + 2

    cites.sort(key=lambda c: c[0])
    usage = data.get("usage") or {}
    return EngineResult(
        engine="openai", model=data.get("model") or model, text="\n\n".join(texts),
        citations=[c for _, c in cites], sources=sources, search_queries=queries,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        search_calls=search_calls,
    )
