"""Run one monitoring cycle: every question x every engine."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .analyzer import analyze
from .config import MARKET_COUNTRY, Config
from .db import DB
from .engines import ENGINES, LABELS, EngineError
from .questions import Question, load_questions

log = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def available_engines(cfg: Config, names: list[str]) -> list[str]:
    out = []
    for name in names:
        if name not in ENGINES:
            log.warning("未知引擎 %s，已跳过（可选: %s）", name, ", ".join(ENGINES))
        elif not cfg.api_key(name):
            log.warning("%s 没有配置 API 密钥，本次跳过", LABELS.get(name, name))
        else:
            out.append(name)
    return out


def _run_engine(cfg: Config, db: DB, lock: threading.Lock, run_id: int,
                name: str, questions: list[Question]) -> dict:
    engine = ENGINES[name](cfg)
    stats = {"ok": 0, "error": 0, "mentioned": 0}
    fatal = ""
    for i, q in enumerate(questions, 1):
        row = {
            "run_id": run_id, "checked_at": now(), "question_id": q.id, "engine": name,
            "model": engine.model, "language": q.language, "market": q.market,
            "category": q.category, "priority": q.priority, "prompt": q.prompt,
            "expected_url": q.expected_url,
        }
        if fatal:
            row["error"] = f"跳过: {fatal}"
            stats["error"] += 1
        else:
            try:
                res = engine.ask(q.prompt, MARKET_COUNTRY.get(q.market))
                a = analyze(res, cfg.target_domain, cfg.brand_terms, cfg.ignore_domains, q.expected_url)
                row.update({
                    "model": res.model or engine.model,
                    "mentioned": a.mentioned, "cited": a.cited, "brand_in_text": a.brand_in_text,
                    "rank": a.rank, "expected_hit": a.expected_hit,
                    "target_urls": a.target_urls, "competitors": a.competitors,
                    "cited_domains": a.cited_domains, "search_queries": res.search_queries,
                    "answer_text": res.text, "input_tokens": res.input_tokens,
                    "output_tokens": res.output_tokens, "search_calls": res.search_calls,
                })
                stats["ok"] += 1
                stats["mentioned"] += int(a.mentioned)
            except EngineError as exc:
                msg = str(exc)
                row["error"] = msg[:1000]
                stats["error"] += 1
                if msg.startswith(("HTTP 401", "HTTP 403")):
                    fatal = f"{LABELS[name]} 密钥无效或无权限（{msg[:60]}）"
                    log.error(fatal)
            except Exception as exc:  # keep going on unexpected parse errors
                log.exception("%s 处理 %s 时出错", name, q.id)
                row["error"] = f"{type(exc).__name__}: {exc}"[:1000]
                stats["error"] += 1
        with lock:
            db.insert("results", row)
        log.info("[%s] %d/%d %s %s", LABELS.get(name, name), i, len(questions), q.id,
                 "出错" if row.get("error") else ("✓ 提及" if row.get("mentioned") else "未提及"))
        if not fatal and cfg.delay_between_calls:
            time.sleep(cfg.delay_between_calls)
    return stats


def run_monitor(cfg: Config, db: DB, engines: list[str] | None = None,
                priorities: list[str] | None = None, limit: int | None = None,
                note: str = "") -> int:
    priorities = priorities or cfg.priorities
    questions = load_questions(cfg.questions_file, priorities)
    if limit:
        questions = questions[:limit]
    if not questions:
        raise SystemExit(f"问题清单里没有符合条件的问题（优先级 {priorities}）")
    names = available_engines(cfg, engines or cfg.engines)
    if not names:
        raise SystemExit("没有可用的引擎：请在 .env 里填写至少一个 API 密钥")

    run_id = db.insert("runs", {
        "started_at": now(), "status": "running", "engines": ",".join(names),
        "priorities": ",".join(priorities), "question_count": len(questions), "note": note,
    })
    log.info("开始第 %d 次检测：%d 个问题 × %s", run_id, len(questions), ", ".join(LABELS[n] for n in names))
    lock = threading.Lock()
    totals: dict[str, dict] = {}
    status = "done"
    try:
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futs = {n: pool.submit(_run_engine, cfg, db, lock, run_id, n, questions) for n in names}
            for n, f in futs.items():
                totals[n] = f.result()
    except BaseException:
        status = "failed"
        raise
    finally:
        db.execute("UPDATE runs SET finished_at = %s, status = %s WHERE id = %s", (now(), status, run_id))
    for n, s in totals.items():
        log.info("%s：成功 %d，出错 %d，提及 %d", LABELS[n], s["ok"], s["error"], s["mentioned"])
    return run_id
