"""Offline engine with fake answers, for testing the pipeline without API keys."""
from __future__ import annotations

import hashlib

from .base import Citation, Engine, EngineResult

SUPPLIERS = [
    "competitor-a.example", "competitor-b.example", "competitor-c.example",
    "competitor-d.example", "wikipedia.org",
]


class MockEngine(Engine):
    name = "mock"

    @property
    def model(self) -> str:
        return "mock-1"

    def ask(self, prompt: str, country: str | None = None) -> EngineResult:
        h = int(hashlib.md5(prompt.encode()).hexdigest(), 16)
        picks = [SUPPLIERS[(h >> i) % len(SUPPLIERS)] for i in range(0, 12, 4)]
        cites = [Citation(url=f"https://{d}/page", title=d) for d in dict.fromkeys(picks)]
        text = "（模拟回答）以下是一些可以考虑的供应商：" + "、".join(c.title for c in cites)
        if h % 3 == 0:
            pos = h % (len(cites) + 1)
            cites.insert(pos, Citation(url=f"https://{self.cfg.target_domain}/products/", title=self.cfg.site_name))
            text += "，以及 Demo Works（示例城市）"
        return EngineResult(engine="mock", model=self.model, text=text, citations=cites,
                            search_queries=[prompt], input_tokens=50, output_tokens=200, search_calls=1)
