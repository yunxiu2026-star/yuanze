"""Count visits referred by AI assistants, and AI crawler hits, from Nginx access logs.

Expects the default "combined" log format:
  $remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"
No change to the website or to Nginx is needed: the logs are only read.
"""
from __future__ import annotations

import glob
import gzip
import logging
import re
from collections import Counter
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

LINE_RE = re.compile(
    r'^\S+ \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<target>\S+)[^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
)

# Referrer host -> source label
REFERRER_HOSTS = {
    "chatgpt.com": "ChatGPT", "chat.openai.com": "ChatGPT",
    "perplexity.ai": "Perplexity",
    "gemini.google.com": "Gemini", "bard.google.com": "Gemini",
    "copilot.microsoft.com": "Copilot",
    "claude.ai": "Claude",
    "chat.deepseek.com": "DeepSeek",
    "grok.com": "Grok",
    "meta.ai": "Meta AI",
    "chat.mistral.ai": "Mistral",
    "you.com": "You.com",
    "kimi.com": "Kimi", "kimi.moonshot.cn": "Kimi",
    "doubao.com": "豆包",
}
# utm_source value fragments -> source label (ChatGPT adds utm_source=chatgpt.com)
UTM_SOURCES = {
    "chatgpt": "ChatGPT", "openai": "ChatGPT", "perplexity": "Perplexity",
    "gemini": "Gemini", "copilot": "Copilot", "claude": "Claude", "deepseek": "DeepSeek",
    "grok": "Grok",
}
# User-agent fragment -> crawler label
AI_BOTS = [
    ("OAI-SearchBot", "OAI-SearchBot (ChatGPT 搜索索引)"),
    ("ChatGPT-User", "ChatGPT-User (用户提问时实时访问)"),
    ("GPTBot", "GPTBot (OpenAI 训练抓取)"),
    ("Perplexity-User", "Perplexity-User (用户提问时实时访问)"),
    ("PerplexityBot", "PerplexityBot (Perplexity 索引)"),
    ("Claude-User", "Claude-User (用户提问时实时访问)"),
    ("Claude-SearchBot", "Claude-SearchBot (Claude 搜索索引)"),
    ("ClaudeBot", "ClaudeBot (Anthropic 训练抓取)"),
    ("Google-CloudVertexBot", "Google-CloudVertexBot"),
    ("meta-externalagent", "Meta AI 抓取"),
    ("Bytespider", "Bytespider (字节跳动)"),
    ("Amazonbot", "Amazonbot"),
    ("CCBot", "CCBot (Common Crawl)"),
]
ASSET_EXT = re.compile(
    r"\.(?:js|mjs|css|map|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|otf|eot|mp4|webm|mp3|"
    r"json|xml|txt|pdf|zip|gz)$", re.I)
GENERIC_BOT = re.compile(r"bot|crawl|spider|slurp|headless|curl|wget|python-requests", re.I)


def detect_source(referer: str, target: str) -> str | None:
    if referer and referer != "-":
        host = (urlsplit(referer).hostname or "").lower().removeprefix("www.")
        for h, label in REFERRER_HOSTS.items():
            if host == h or host.endswith("." + h):
                return label
    try:
        qs = parse_qs(urlsplit(target).query)
    except ValueError:
        return None
    for val in qs.get("utm_source", []):
        v = val.lower()
        for frag, label in UTM_SOURCES.items():
            if frag in v:
                return label
    return None


def detect_bot(ua: str) -> str | None:
    for frag, label in AI_BOTS:
        if frag.lower() in ua.lower():
            return label
    return None


def _open(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def parse_lines(lines, target: str = ""):
    """Return (referrals Counter[(day, source, path)], crawlers Counter[(day, bot)], parsed, skipped)."""
    refs: Counter = Counter()
    bots: Counter = Counter()
    parsed = skipped = 0
    for line in lines:
        m = LINE_RE.match(line)
        if not m:
            skipped += 1
            continue
        parsed += 1
        try:
            day = datetime.strptime(m["time"], "%d/%b/%Y:%H:%M:%S %z").date().isoformat()
        except ValueError:
            skipped += 1
            continue
        ua = m["ua"]
        bot = detect_bot(ua)
        if bot:
            bots[(day, bot)] += 1
            continue
        if m["method"] != "GET" or m["status"] not in ("200", "304"):
            continue
        target_url = m["target"]
        path = urlsplit(target_url).path or "/"
        if ASSET_EXT.search(path) or path.startswith("/api/") or GENERIC_BOT.search(ua):
            continue
        source = detect_source(m["referer"], target_url)
        if source:
            refs[(day, source, path)] += 1
    return refs, bots, parsed, skipped


def ingest(db, pattern: str) -> dict:
    files = sorted(glob.glob(pattern))
    if not files:
        log.warning("没有找到 Nginx 日志: %s（跳过来源统计）", pattern)
        return {"files": 0, "parsed": 0, "skipped": 0, "referrals": 0, "crawlers": 0}
    refs: Counter = Counter()
    bots: Counter = Counter()
    parsed = skipped = 0
    for f in files:
        try:
            with _open(f) as fh:
                r, b, p, s = parse_lines(fh)
        except OSError as exc:
            log.warning("无法读取日志 %s: %s", f, exc)
            continue
        refs.update(r)
        bots.update(b)
        parsed += p
        skipped += s
    for (day, source, path), n in refs.items():
        db.upsert_max("ai_referrals", {"day": day, "source": source, "path": path}, n)
    for (day, bot), n in bots.items():
        db.upsert_max("ai_crawlers", {"day": day, "bot": bot}, n)
    if parsed == 0 and skipped:
        log.warning("日志格式不是 Nginx 默认的 combined 格式，未能解析（%d 行）", skipped)
    return {"files": len(files), "parsed": parsed, "skipped": skipped,
            "referrals": sum(refs.values()), "crawlers": sum(bots.values())}
