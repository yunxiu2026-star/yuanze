"""Long-running loop: read Nginx logs daily, run the full check weekly, and
pick up "run now" requests made from the web dashboard."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Config
from .db import DB

log = logging.getLogger(__name__)
WEEKDAYS = "一二三四五六日"
POLL_SECONDS = 30


def next_fire(now: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    fire = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if fire <= now:
        fire += timedelta(days=1)
    return fire


def daily_job(cfg: Config, db: DB, weekly: bool) -> None:
    from .cli import do_weekly
    from .nginx_logs import ingest
    try:
        stats = ingest(db, cfg.nginx_log_glob)
        log.info("日志统计完成: %s", stats)
    except Exception:
        log.exception("日志统计失败")
    if weekly:
        try:
            do_weekly(cfg, db, note="weekly")
        except BaseException as exc:  # noqa: BLE001 - never let the loop die
            if isinstance(exc, KeyboardInterrupt):
                raise
            log.exception("每周检测失败")


def _split(s: str | None) -> list[str] | None:
    return [x for x in (s or "").split(",") if x] or None


def process_requests(cfg: Config, db: DB) -> int:
    """Run pending dashboard requests one at a time. Returns how many ran."""
    from .cli import do_weekly
    done = 0
    while True:
        pending = db.query("SELECT * FROM run_requests WHERE status = 'pending' ORDER BY id LIMIT 1")
        if not pending:
            return done
        req = pending[0]
        db.execute("UPDATE run_requests SET status = 'running' WHERE id = %s", (req["id"],))
        log.info("执行网页提交的检测请求 #%d", req["id"])
        try:
            do_weekly(cfg, db, engines=_split(req.get("engines")), priorities=_split(req.get("priorities")),
                      limit=req.get("limit_n") or None, note=f"web#{req['id']}")
            run = db.query("SELECT id FROM runs ORDER BY id DESC LIMIT 1")
            db.execute("UPDATE run_requests SET status = 'done', run_id = %s WHERE id = %s",
                       (run[0]["id"] if run else None, req["id"]))
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            log.exception("网页检测请求 #%d 失败", req["id"])
            db.execute("UPDATE run_requests SET status = 'failed', error = %s WHERE id = %s",
                       (str(exc)[:500] or type(exc).__name__, req["id"]))
        done += 1


def recover_stale(db: DB) -> None:
    """A restart while a run was in progress leaves 'running' rows behind."""
    db.execute("UPDATE run_requests SET status = 'failed', error = %s WHERE status = 'running'",
               ("程序重启，检测中断",))
    db.execute("UPDATE runs SET status = 'failed', finished_at = %s WHERE status = 'running'",
               (datetime.now(timezone.utc).isoformat(),))


def run_forever(cfg: Config, db: DB) -> None:
    tz = ZoneInfo(cfg.timezone)
    recover_stale(db)
    log.info("定时任务已启动：每天 %s 统计日志，每周%s %s 运行完整检测（时区 %s）",
             cfg.schedule_time, WEEKDAYS[cfg.schedule_weekday], cfg.schedule_time, cfg.timezone)
    fire = next_fire(datetime.now(tz), cfg.schedule_time)
    log.info("下次定时运行: %s", fire.strftime("%Y-%m-%d %H:%M %Z"))
    while True:
        try:
            process_requests(cfg, db)
        except Exception:
            log.exception("读取网页检测请求失败")
        if datetime.now(tz) >= fire:
            daily_job(cfg, db, weekly=fire.weekday() == cfg.schedule_weekday)
            fire = next_fire(datetime.now(tz), cfg.schedule_time)
            log.info("下次定时运行: %s", fire.strftime("%Y-%m-%d %H:%M %Z"))
        time.sleep(POLL_SECONDS)
