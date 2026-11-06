"""Configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _float(name: str, default: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# Generic / platform domains that are not suppliers: they are kept in the
# citation list but excluded from "rank" and from the competitor list.
DEFAULT_IGNORE_DOMAINS = (
    "wikipedia.org,reddit.com,youtube.com,quora.com,linkedin.com,facebook.com,"
    "instagram.com,tiktok.com,x.com,twitter.com,medium.com,pinterest.com,"
    "google.com,bing.com,forbes.com,statista.com,fda.gov,europa.eu,gov.uk"
)

# Market label in the question list -> ISO country code used as approximate
# user location, so answers resemble what a buyer in that market would see.
MARKET_COUNTRY = {
    "全球/英语市场": "US",
    "美国": "US",
    "欧盟": "DE",
    "中东": "AE",
    "中东(沙特/阿联酋)": "AE",
    "韩国": "KR",
    "印尼": "ID",
    "澳洲": "AU",
    "拉美/西班牙": "MX",
    "巴西/葡萄牙": "BR",
    "法国/北非": "FR",
    "俄罗斯/独联体": "KZ",
    "越南": "VN",
    "泰国": "TH",
    "马来西亚": "MY",
}


@dataclass
class Config:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", "sqlite:///data/geo_monitor.db"))
    questions_file: str = field(default_factory=lambda: os.environ.get(
        "QUESTIONS_FILE", "data/questions.xlsx"))
    reports_dir: str = field(default_factory=lambda: os.environ.get("REPORTS_DIR", "reports"))

    target_domain: str = field(default_factory=lambda: os.environ.get(
        "TARGET_DOMAIN", "example.test").lower())
    brand_terms: list[str] = field(default_factory=lambda: _list(
        "BRAND_TERMS", "Demo Works,demo-brand,示例品牌"))
    ignore_domains: list[str] = field(default_factory=lambda: _list(
        "IGNORE_DOMAINS", DEFAULT_IGNORE_DOMAINS))

    engines: list[str] = field(default_factory=lambda: _list("ENGINES", "openai,gemini,perplexity"))
    priorities: list[str] = field(default_factory=lambda: _list("PRIORITIES", "P1"))

    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-5.5"))
    openai_reasoning_effort: str = field(default_factory=lambda: os.environ.get(
        "OPENAI_REASONING_EFFORT", "low"))
    openai_search_context: str = field(default_factory=lambda: os.environ.get(
        "OPENAI_SEARCH_CONTEXT", "medium"))

    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"))
    gemini_resolve_redirects: bool = field(default_factory=lambda: _bool("GEMINI_RESOLVE_REDIRECTS", True))

    perplexity_api_key: str = field(default_factory=lambda: os.environ.get("PERPLEXITY_API_KEY", ""))
    perplexity_preset: str = field(default_factory=lambda: os.environ.get("PERPLEXITY_PRESET", "fast"))
    perplexity_model: str = field(default_factory=lambda: os.environ.get("PERPLEXITY_MODEL", ""))

    request_timeout: float = field(default_factory=lambda: _float("REQUEST_TIMEOUT", 120.0))
    delay_between_calls: float = field(default_factory=lambda: _float("DELAY_BETWEEN_CALLS", 1.0))

    nginx_log_glob: str = field(default_factory=lambda: os.environ.get(
        "NGINX_LOG_GLOB", "/var/log/nginx/access.log*"))

    timezone: str = field(default_factory=lambda: os.environ.get("TZ", "Asia/Shanghai"))
    schedule_weekday: int = field(default_factory=lambda: int(os.environ.get("SCHEDULE_WEEKDAY", "0")))
    schedule_time: str = field(default_factory=lambda: os.environ.get("SCHEDULE_TIME", "09:17"))

    # Optional cost estimate (USD). Leave empty to report usage only.
    price: dict = field(default_factory=lambda: {
        "openai": (_float("OPENAI_PRICE_IN_PER_M"), _float("OPENAI_PRICE_OUT_PER_M"),
                   _float("OPENAI_PRICE_SEARCH_PER_K")),
        "gemini": (_float("GEMINI_PRICE_IN_PER_M"), _float("GEMINI_PRICE_OUT_PER_M"),
                   _float("GEMINI_PRICE_SEARCH_PER_K")),
        "perplexity": (_float("PERPLEXITY_PRICE_IN_PER_M"), _float("PERPLEXITY_PRICE_OUT_PER_M"),
                       _float("PERPLEXITY_PRICE_SEARCH_PER_K")),
    })

    # Web dashboard
    web_password: str = field(default_factory=lambda: os.environ.get("WEB_PASSWORD", ""))
    web_secret: str = field(default_factory=lambda: os.environ.get("WEB_SECRET", ""))
    web_cookie_secure: bool = field(default_factory=lambda: _bool("WEB_COOKIE_SECURE", True))
    site_name: str = field(default_factory=lambda: os.environ.get("SITE_NAME", "Demo Works"))

    # Optional e-mail delivery of the weekly report.
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "465") or 465))
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    report_email_to: list[str] = field(default_factory=lambda: _list("REPORT_EMAIL_TO", ""))

    def api_key(self, engine: str) -> str:
        return {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "perplexity": self.perplexity_api_key,
            "mock": "mock",
        }.get(engine, "")
