"""Turn an engine answer into monitoring facts: mentioned? cited? rank? competitors?"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .engines import Citation, EngineResult


def domain_of(c: Citation) -> str:
    if c.domain:
        return c.domain
    host = (urlsplit(c.url).hostname or "").lower()
    return host.removeprefix("www.")


def matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def norm_path(url_or_path: str) -> str:
    path = urlsplit(url_or_path).path if "://" in url_or_path else url_or_path.split("?")[0]
    path = path or "/"
    if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    return path


@dataclass
class Analysis:
    mentioned: bool = False         # cited or named in the answer text
    cited: bool = False             # our site is among the cited sources
    brand_in_text: bool = False     # brand name appears in the answer text
    rank: int | None = None         # position among cited supplier domains (1 = first)
    target_urls: list[str] = field(default_factory=list)
    expected_hit: bool = False      # the expected page was among the cited pages
    competitors: list[str] = field(default_factory=list)
    cited_domains: list[str] = field(default_factory=list)


def analyze(result: EngineResult, target: str, brand_terms: list[str],
            ignore_domains: list[str], expected_url: str = "") -> Analysis:
    a = Analysis()
    text = (result.text or "").casefold()
    a.brand_in_text = any(t.casefold() in text for t in brand_terms if t)

    cites = result.citations or result.sources
    supplier_order: list[str] = []
    for c in cites:
        dom = domain_of(c)
        if not dom:
            continue
        if dom not in a.cited_domains:
            a.cited_domains.append(dom)
        if matches(dom, target):
            a.cited = True
            if c.url and "vertexaisearch" not in c.url and c.url not in a.target_urls:
                a.target_urls.append(c.url)
            if target not in supplier_order:
                supplier_order.append(target)
        elif not any(matches(dom, ig) for ig in ignore_domains):
            if dom not in supplier_order:
                supplier_order.append(dom)

    if a.cited:
        a.rank = supplier_order.index(target) + 1
    a.competitors = [d for d in supplier_order if d != target]
    a.mentioned = a.cited or a.brand_in_text

    if expected_url and a.target_urls:
        want = norm_path(expected_url)
        a.expected_hit = any(norm_path(u) == want for u in a.target_urls)
    return a
