"""Read-side aggregations for the dashboard. Data volume is small (a few
thousand rows a year), so most aggregation happens in Python for clarity
and SQLite/PostgreSQL parity."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from ..db import DB, decode_result
from ..engines import LABELS

PRI_ORDER = {"P1": 0, "P2": 1, "P3": 2}
ENGINE_ORDER = ["openai", "gemini", "perplexity", "mock"]
TREND_MIN_QUESTIONS = 10  # runs smaller than this are test runs; kept out of the trend


def label(engine: str) -> str:
    return LABELS.get(engine, engine)


def sort_engines(names) -> list[str]:
    return sorted(set(names), key=lambda e: (ENGINE_ORDER.index(e) if e in ENGINE_ORDER else 99, e))


def done_runs(db: DB, limit: int = 100) -> list[dict]:
    return [decode_result(r) for r in db.query(
        "SELECT * FROM runs WHERE status = 'done' ORDER BY id DESC LIMIT %s", (limit,))]


def all_runs(db: DB, limit: int = 50) -> list[dict]:
    return [decode_result(r) for r in db.query("SELECT * FROM runs ORDER BY id DESC LIMIT %s", (limit,))]


def get_run(db: DB, run_id: int | None) -> dict | None:
    if run_id:
        rows = db.query("SELECT * FROM runs WHERE id = %s", (run_id,))
    else:
        rows = db.query("SELECT * FROM runs WHERE status = 'done' ORDER BY id DESC LIMIT 1")
    return decode_result(rows[0]) if rows else None


def run_rows(db: DB, run_id: int) -> list[dict]:
    return [decode_result(r) for r in db.query(
        "SELECT * FROM results WHERE run_id = %s ORDER BY question_id, engine", (run_id,))]


def previous_rows(db: DB, run: dict, rows: list[dict]) -> tuple[dict | None, list[dict]]:
    prev = db.query("SELECT * FROM runs WHERE status = 'done' AND id < %s ORDER BY id DESC LIMIT 1",
                    (run["id"],))
    if not prev:
        return None, []
    p = decode_result(prev[0])
    keys = {(r["question_id"], r["engine"]) for r in rows}
    prows = [decode_result(r) for r in db.query("SELECT * FROM results WHERE run_id = %s", (p["id"],))]
    return p, [r for r in prows if (r["question_id"], r["engine"]) in keys]


def _stats(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r.get("error")]
    m = sum(1 for r in ok if r.get("mentioned"))
    ranks = [r["rank"] for r in ok if r.get("rank")]
    return {
        "valid": len(ok), "mentioned": m, "rate": (m / len(ok)) if ok else None,
        "cited": sum(1 for r in ok if r.get("cited")),
        "expected": sum(1 for r in ok if r.get("expected_hit")),
        "avg_rank": (sum(ranks) / len(ranks)) if ranks else None,
        "errors": len(rows) - len(ok),
    }


def group_summary(rows: list[dict], prev_rows: list[dict], key: str) -> list[dict]:
    groups = defaultdict(list)
    pgroups = defaultdict(list)
    for r in rows:
        groups[r.get(key)].append(r)
    for r in prev_rows:
        pgroups[r.get(key)].append(r)
    names = sort_engines(groups) if key == "engine" else list(dict.fromkeys(r.get(key) for r in rows))
    out = []
    for name in names:
        s = _stats(groups[name])
        prev = _stats(pgroups[name])["rate"] if pgroups.get(name) else None
        s.update(name=name, label=label(name) if key == "engine" else name, prev_rate=prev,
                 delta=(s["rate"] - prev) if s["rate"] is not None and prev is not None else None)
        out.append(s)
    return out


def total_summary(rows: list[dict], prev_rows: list[dict]) -> dict:
    s = _stats(rows)
    prev = _stats(prev_rows)["rate"] if prev_rows else None
    s["prev_rate"] = prev
    s["delta"] = (s["rate"] - prev) if s["rate"] is not None and prev is not None else None
    return s


def trend(db: DB, n: int = 12) -> dict:
    runs = [r for r in done_runs(db, 200) if (r.get("question_count") or 0) >= TREND_MIN_QUESTIONS][:n]
    runs.reverse()
    if not runs:
        return {"runs": [], "engines": []}
    ids = [r["id"] for r in runs]
    marks = ",".join(["%s"] * len(ids))
    rows = db.query(f"SELECT run_id, engine, mentioned, error FROM results WHERE run_id IN ({marks})", ids)
    acc: dict = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["error"]:
            continue
        a = acc[(r["run_id"], r["engine"])]
        a[0] += 1
        a[1] += 1 if r["mentioned"] else 0
    engines = sort_engines(e for (_, e) in acc)
    series = []
    for e in engines:
        pts = []
        for run in runs:
            v, m = acc.get((run["id"], e), (0, 0))
            pts.append((m / v) if v else None)
        series.append({"engine": e, "label": label(e), "points": pts})
    return {"runs": runs, "engines": engines, "series": series}


def changes(rows: list[dict], prev_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    before = {(r["question_id"], r["engine"]): r for r in prev_rows if not r.get("error")}
    gained, lost = [], []
    for r in rows:
        if r.get("error"):
            continue
        b = before.get((r["question_id"], r["engine"]))
        if not b:
            continue
        if r.get("mentioned") and not b.get("mentioned"):
            gained.append({**r, "prev_rank": b.get("rank")})
        elif b.get("mentioned") and not r.get("mentioned"):
            lost.append({**r, "prev_rank": b.get("rank")})
    key = lambda x: (PRI_ORDER.get(x.get("priority"), 9), x["question_id"], x["engine"])  # noqa: E731
    return sorted(gained, key=key), sorted(lost, key=key)


def gaps(rows: list[dict]) -> list[dict]:
    by_q = defaultdict(list)
    for r in rows:
        by_q[r["question_id"]].append(r)
    out = []
    for qid, rs in by_q.items():
        ok = [r for r in rs if not r.get("error")]
        if ok and not any(r.get("mentioned") for r in ok):
            comp = Counter(d for r in ok for d in (r.get("competitors") or [])[:5])
            out.append({**rs[0], "engines_checked": [label(r["engine"]) for r in ok],
                        "top_competitors": [d for d, _ in comp.most_common(5)]})
    return sorted(out, key=lambda x: (PRI_ORDER.get(x.get("priority"), 9), x["question_id"]))


def competitors(rows: list[dict], limit: int = 50) -> list[dict]:
    count: Counter = Counter()
    qs: dict = defaultdict(set)
    es: dict = defaultdict(set)
    ranks: dict = defaultdict(list)
    for r in rows:
        for i, d in enumerate(r.get("competitors") or []):
            count[d] += 1
            qs[d].add(r["question_id"])
            es[d].add(label(r["engine"]))
            ranks[d].append(i + 1)
    total_q = len({r["question_id"] for r in rows if not r.get("error")}) or 1
    return [{"domain": d, "hits": c, "questions": len(qs[d]), "share": len(qs[d]) / total_q,
             "engines": sorted(es[d]), "avg_pos": sum(ranks[d]) / len(ranks[d])}
            for d, c in count.most_common(limit)]


def traffic(db: DB, today: date, days: int = 7) -> dict:
    start = today - timedelta(days=days)
    prev_start = start - timedelta(days=days)
    s, e, ps = start.isoformat(), (today + timedelta(days=1)).isoformat(), prev_start.isoformat()
    cur = db.query("SELECT source, SUM(hits) AS hits FROM ai_referrals WHERE day >= %s AND day < %s "
                   "GROUP BY source", (s, e))
    pre = {x["source"]: int(x["hits"]) for x in db.query(
        "SELECT source, SUM(hits) AS hits FROM ai_referrals WHERE day >= %s AND day < %s GROUP BY source",
        (ps, s))}
    sources = sorted(({"source": x["source"], "hits": int(x["hits"]), "prev": pre.get(x["source"], 0)}
                      for x in cur), key=lambda x: -x["hits"])
    for src, n in pre.items():
        if src not in {x["source"] for x in sources}:
            sources.append({"source": src, "hits": 0, "prev": n})
    pages = [{"path": p["path"], "source": p["source"], "hits": int(p["hits"])} for p in db.query(
        "SELECT path, source, SUM(hits) AS hits FROM ai_referrals WHERE day >= %s AND day < %s "
        "GROUP BY path, source ORDER BY SUM(hits) DESC LIMIT 30", (s, e))]
    pre_b = {x["bot"]: int(x["hits"]) for x in db.query(
        "SELECT bot, SUM(hits) AS hits FROM ai_crawlers WHERE day >= %s AND day < %s GROUP BY bot", (ps, s))}
    bots = [{"bot": b["bot"], "hits": int(b["hits"]), "prev": pre_b.get(b["bot"], 0)} for b in db.query(
        "SELECT bot, SUM(hits) AS hits FROM ai_crawlers WHERE day >= %s AND day < %s "
        "GROUP BY bot ORDER BY SUM(hits) DESC", (s, e))]
    last = db.query("SELECT MAX(day) AS d FROM ai_referrals")[0]["d"]
    last_b = db.query("SELECT MAX(day) AS d FROM ai_crawlers")[0]["d"]
    latest = max([str(x) for x in (last, last_b) if x], default=None)
    return {"start": start, "end": today, "days": days, "sources": sources, "pages": pages, "bots": bots,
            "total": sum(x["hits"] for x in sources), "total_prev": sum(x["prev"] for x in sources),
            "latest_day": latest}


def latest_by_question(db: DB) -> dict:
    """question_id -> engine -> most recent successful result row (any run)."""
    rows = db.query("SELECT r.question_id, r.engine, r.mentioned, r.rank, r.id, r.run_id "
                    "FROM results r JOIN runs u ON u.id = r.run_id "
                    "WHERE r.error IS NULL AND u.status = 'done' ORDER BY r.run_id")
    out: dict = defaultdict(dict)
    for r in rows:
        out[r["question_id"]][r["engine"]] = {"mentioned": bool(r["mentioned"]), "rank": r["rank"],
                                              "id": r["id"], "run_id": r["run_id"]}
    return out


def question_history(db: DB, qid: str) -> list[dict]:
    return [decode_result(r) for r in db.query(
        "SELECT r.* FROM results r JOIN runs u ON u.id = r.run_id WHERE r.question_id = %s "
        "ORDER BY r.run_id DESC, r.engine", (qid,))]


def filter_rows(rows: list[dict], engine: str = "", language: str = "", priority: str = "",
                status: str = "", q: str = "") -> list[dict]:
    out = []
    ql = q.casefold().strip()
    for r in rows:
        if engine and r["engine"] != engine:
            continue
        if language and r.get("language") != language:
            continue
        if priority and r.get("priority") != priority:
            continue
        if status == "yes" and not (r.get("mentioned") and not r.get("error")):
            continue
        if status == "no" and (r.get("mentioned") or r.get("error")):
            continue
        if status == "error" and not r.get("error"):
            continue
        if ql and ql not in (r.get("prompt") or "").casefold() and ql not in r["question_id"].casefold():
            continue
        out.append(r)
    def key(x):
        e = x["engine"]
        return (PRI_ORDER.get(x.get("priority"), 9), x["question_id"],
                ENGINE_ORDER.index(e) if e in ENGINE_ORDER else 99)
    return sorted(out, key=key)
