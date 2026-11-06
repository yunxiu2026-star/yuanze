"""Command line entry point: python -m geo_monitor <command>."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import MARKET_COUNTRY, Config
from .db import DB

log = logging.getLogger("geo_monitor")


def load_dotenv(path: str = ".env") -> None:
    """Read KEY=VALUE lines from .env into the environment (existing variables win).
    Docker already does this via env_file; this covers running without Docker."""
    import os
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _list(s: str | None) -> list[str] | None:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def summarize(db: DB, run_id: int) -> str:
    from .engines import LABELS
    rows = db.query("SELECT engine, mentioned, error FROM results WHERE run_id = %s", (run_id,))
    lines = [f"第 {run_id} 次检测结果："]
    for e in dict.fromkeys(r["engine"] for r in rows):
        ok = [r for r in rows if r["engine"] == e and not r["error"]]
        m = sum(1 for r in ok if r["mentioned"])
        err = sum(1 for r in rows if r["engine"] == e and r["error"])
        rate = f"{m / len(ok):.0%}" if ok else "-"
        lines.append(f"  {LABELS.get(e, e)}: {m}/{len(ok)} 个问题提及本站（{rate}）" + (f"，出错 {err}" if err else ""))
    return "\n".join(lines)


def do_weekly(cfg: Config, db: DB, **kw) -> str:
    from .mailer import send_report
    from .report import build_report
    from .runner import run_monitor
    run_id = run_monitor(cfg, db, **kw)
    path = build_report(cfg, db, run_id)
    summary = summarize(db, run_id)
    log.info("%s\n周报已生成: %s", summary, path)
    send_report(cfg, path, summary)
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="geo_monitor", description="AI 推荐（GEO）监测程序")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="创建数据库表")

    r = sub.add_parser("run", help="立即运行一次完整检测并生成周报")
    r.add_argument("--engines", help="只用这些引擎，逗号分隔：openai,gemini,perplexity,mock")
    r.add_argument("--priorities", help="只检测这些优先级，如 P1 或 P1,P2")
    r.add_argument("--limit", type=int, help="只检测前 N 个问题（试跑用）")
    r.add_argument("--no-report", action="store_true", help="不生成周报")

    w = sub.add_parser("web", help="启动网页后台")
    w.add_argument("--host", default="0.0.0.0")
    w.add_argument("--port", type=int, default=8000)

    t = sub.add_parser("test", help="用一个问题测试某个引擎的密钥和返回结果")
    t.add_argument("engine", choices=["openai", "gemini", "perplexity", "mock"])
    t.add_argument("--prompt", default="best private label cosmetics manufacturer in China")
    t.add_argument("--market", default="美国")
    t.add_argument("--raw", action="store_true", help="同时打印原始返回")

    rp = sub.add_parser("report", help="为某次检测重新生成周报")
    rp.add_argument("--run-id", type=int, help="默认最近一次")

    sub.add_parser("ingest-logs", help="立即读取 Nginx 日志，统计 AI 来源访问和 AI 爬虫")
    sub.add_parser("questions", help="列出当前会检测的问题")
    sub.add_parser("scheduler", help="常驻运行：每天统计日志，每周检测（Docker 默认命令）")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    cfg = Config()

    if args.cmd == "questions":
        from .questions import load_questions
        qs = load_questions(cfg.questions_file, cfg.priorities)
        for q in qs:
            print(f"{q.id}\t{q.priority}\t{q.language}\t{q.prompt}")
        print(f"共 {len(qs)} 个（优先级 {','.join(cfg.priorities)}）")
        return 0

    if args.cmd == "test":
        from .analyzer import analyze
        from .engines import ENGINES
        if not cfg.api_key(args.engine):
            print(f"没有配置 {args.engine} 的 API 密钥", file=sys.stderr)
            return 2
        eng = ENGINES[args.engine](cfg)
        if args.raw:
            orig = eng.post_json

            def spy(url, headers, body, attempts=4):
                data = orig(url, headers, body, attempts)
                print(json.dumps(data, ensure_ascii=False, indent=2)[:20000])
                return data
            eng.post_json = spy  # type: ignore[method-assign]
        res = eng.ask(args.prompt, MARKET_COUNTRY.get(args.market))
        a = analyze(res, cfg.target_domain, cfg.brand_terms, cfg.ignore_domains)
        print(f"模型: {res.model}\n搜索词: {res.search_queries}\n搜索次数: {res.search_calls}  "
              f"tokens: {res.input_tokens}/{res.output_tokens}")
        print("引用来源:")
        for i, c in enumerate(res.citations, 1):
            print(f"  {i}. {c.domain or ''} {c.url}")
        print(f"\n是否提及本站: {a.mentioned}（引用 {a.cited}，正文出现品牌名 {a.brand_in_text}），排名 {a.rank}")
        print(f"竞品: {a.competitors[:10]}")
        print("\n回答节选:\n" + res.text[:1500])
        return 0

    db = DB(cfg.database_url)
    db.init()
    if args.cmd == "init-db":
        print("数据库表已就绪")
    elif args.cmd == "run":
        kw = dict(engines=_list(args.engines), priorities=_list(args.priorities), limit=args.limit,
                  note="manual")
        if args.no_report:
            from .runner import run_monitor
            run_id = run_monitor(cfg, db, **kw)
            print(summarize(db, run_id))
        else:
            print(do_weekly(cfg, db, **kw))
    elif args.cmd == "report":
        from .report import build_report
        print(build_report(cfg, db, args.run_id))
    elif args.cmd == "ingest-logs":
        from .nginx_logs import ingest
        print(ingest(db, cfg.nginx_log_glob))
    elif args.cmd == "web":
        import uvicorn

        from .web.app import create_app
        db.close()
        uvicorn.run(create_app(cfg), host=args.host, port=args.port, proxy_headers=True,
                    forwarded_allow_ips="*", log_level="info")
    elif args.cmd == "scheduler":
        from .scheduler import run_forever
        run_forever(cfg, db)
    return 0
