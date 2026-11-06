"""Weekly Excel report built from one monitoring run."""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .config import Config
from .db import DB, decode_result
from .engines import LABELS

FONT = "Arial"
HDR_FILL = PatternFill("solid", start_color="1F3A2E")
HDR_FONT = Font(name=FONT, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=FONT, bold=True, size=13)
NOTE_FONT = Font(name=FONT, size=9, color="666666")
BODY = Font(name=FONT, size=10)
YES_FILL = PatternFill("solid", start_color="D9EAD3")
NO_FILL = PatternFill("solid", start_color="F4CCCC")
SIDE = Side(style="thin", color="D0D0D0")
BORDER = Border(left=SIDE, right=SIDE, top=SIDE, bottom=SIDE)
PRI_ORDER = {"P1": 0, "P2": 1, "P3": 2}

DETAIL_COLS = ["检测日期", "问题ID", "引擎", "语言", "目标市场", "问题类型", "优先级", "监测问题",
               "是否提及", "是否被引用", "排名", "引用的本站页面", "期望页面", "命中期望页面",
               "同时出现的竞品", "AI 搜索用词", "错误", "输入 tokens", "输出 tokens", "搜索次数"]
DETAIL_WIDTHS = [11, 8, 11, 9, 14, 10, 7, 52, 9, 10, 6, 40, 30, 12, 46, 40, 30, 11, 11, 9]


def _header(ws, row: int, cols: list[str], start_col: int = 1) -> None:
    for j, h in enumerate(cols):
        c = ws.cell(row=row, column=start_col + j, value=h)
        c.fill, c.font, c.border = HDR_FILL, HDR_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _body(cell, wrap: bool = False) -> None:
    cell.font, cell.border = BODY, BORDER
    cell.alignment = Alignment(vertical="top", wrap_text=wrap)


def _yn(v) -> str:
    return "是" if v else "否"


def load_run(db: DB, run_id: int | None) -> tuple[dict, list[dict]]:
    if run_id is None:
        runs = db.query("SELECT * FROM runs WHERE status = 'done' ORDER BY id DESC LIMIT 1")
        if not runs:
            raise SystemExit("还没有完成的检测记录，请先运行一次检测")
    else:
        runs = db.query("SELECT * FROM runs WHERE id = %s", (run_id,))
        if not runs:
            raise SystemExit(f"找不到第 {run_id} 次检测")
    run = decode_result(runs[0])
    rows = [decode_result(r) for r in db.query(
        "SELECT * FROM results WHERE run_id = %s ORDER BY priority, question_id, engine", (run["id"],))]
    return run, rows


def previous_run(db: DB, run: dict) -> tuple[dict | None, list[dict]]:
    prev = db.query("SELECT * FROM runs WHERE status = 'done' AND id < %s ORDER BY id DESC LIMIT 1",
                    (run["id"],))
    if not prev:
        return None, []
    p = decode_result(prev[0])
    rows = [decode_result(r) for r in db.query("SELECT * FROM results WHERE run_id = %s", (p["id"],))]
    return p, rows


def build_report(cfg: Config, db: DB, run_id: int | None = None, out_path: str | None = None) -> str:
    run, rows = load_run(db, run_id)
    prev, prev_rows = previous_run(db, run)
    # Compare like with like: only questions/engines checked in both runs.
    current_keys = {(r["question_id"], r["engine"]) for r in rows}
    prev_rows = [r for r in prev_rows if (r["question_id"], r["engine"]) in current_keys]
    started: datetime = run["started_at"]
    engines = [e for e in (run.get("engines") or "").split(",") if e]

    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True
    ov = wb.active
    ov.title = "概览"
    det = wb.create_sheet("明细")
    _detail_sheet(det, rows)
    n = len(rows) + 1
    _overview_sheet(ov, cfg, run, rows, prev, prev_rows, engines, n)
    _changes_sheet(wb.create_sheet("变化"), rows, prev_rows, prev)
    _missing_sheet(wb.create_sheet("未被推荐的问题"), rows)
    _competitor_sheet(wb.create_sheet("竞品"), rows)
    _traffic_sheet(wb.create_sheet("AI来源访问"), db, started.date())
    _usage_sheet(wb.create_sheet("用量"), cfg, engines, n)

    os.makedirs(cfg.reports_dir, exist_ok=True)
    out_path = out_path or os.path.join(
        cfg.reports_dir, f"GEO周报-{started.date().isoformat()}-第{run['id']}次.xlsx")
    wb.save(out_path)
    return out_path


def _detail_sheet(ws, rows: list[dict]) -> None:
    _header(ws, 1, DETAIL_COLS)
    for i, r in enumerate(rows, 2):
        err = r.get("error") or ""
        vals = [
            r["checked_at"].date().isoformat() if r.get("checked_at") else "",
            r["question_id"], LABELS.get(r["engine"], r["engine"]), r.get("language"),
            r.get("market"), r.get("category"), r.get("priority"), r.get("prompt"),
            "—" if err else _yn(r.get("mentioned")),
            "—" if err else _yn(r.get("cited")),
            r.get("rank") if r.get("rank") else None,
            "\n".join(r.get("target_urls") or []),
            r.get("expected_url"),
            "—" if err or not r.get("cited") else _yn(r.get("expected_hit")),
            "; ".join((r.get("competitors") or [])[:8]),
            "; ".join(r.get("search_queries") or []),
            err or None,
            r.get("input_tokens") or 0, r.get("output_tokens") or 0, r.get("search_calls") or 0,
        ]
        for j, v in enumerate(vals, 1):
            c = ws.cell(row=i, column=j, value=v)
            _body(c, wrap=j in (8, 12, 15, 16, 17))
        mcell = ws.cell(row=i, column=9)
        if mcell.value == "是":
            mcell.fill = YES_FILL
        elif mcell.value == "否":
            mcell.fill = NO_FILL
    for j, w in enumerate(DETAIL_WIDTHS, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(DETAIL_COLS))}{max(len(rows) + 1, 2)}"


def _rate(prev_rows: list[dict], engine: str | None = None, language: str | None = None):
    ok = [r for r in prev_rows if not r.get("error")
          and (engine is None or r["engine"] == engine)
          and (language is None or r.get("language") == language)]
    return (sum(1 for r in ok if r.get("mentioned")) / len(ok)) if ok else None


def _overview_sheet(ws, cfg, run, rows, prev, prev_rows, engines, n) -> None:
    D = "明细"
    rng = lambda col: f"{D}!${col}$2:${col}${max(n, 2)}"  # noqa: E731
    ws["A1"] = f"{cfg.target_domain} · AI 推荐监测周报"
    ws["A1"].font = Font(name=FONT, bold=True, size=15)
    started = run["started_at"]
    ws["A2"] = (f"第 {run['id']} 次检测 · {started.strftime('%Y-%m-%d %H:%M')} UTC · "
                f"{run.get('question_count')} 个问题 · 优先级 {run.get('priorities')}")
    ws["A2"].font = NOTE_FONT
    if prev:
        ws["A3"] = f"对比：第 {prev['id']} 次检测（{prev['started_at'].strftime('%Y-%m-%d')}）"
        ws["A3"].font = NOTE_FONT

    ws["A5"] = "按引擎"
    ws["A5"].font = TITLE_FONT
    cols = ["引擎", "有效检测", "被提及", "提及率", "上次提及率", "变化", "被引用（带链接）",
            "命中期望页面", "平均排名", "出错"]
    _header(ws, 6, cols)
    r = 7
    for e in engines:
        lab = LABELS.get(e, e)
        ws.cell(row=r, column=1, value=lab)
        ws.cell(row=r, column=2, value=f'=COUNTIFS({rng("C")},A{r},{rng("Q")},"")')
        ws.cell(row=r, column=3, value=f'=COUNTIFS({rng("C")},A{r},{rng("I")},"是")')
        ws.cell(row=r, column=4, value=f'=IF(B{r}=0,"-",C{r}/B{r})')
        pr = _rate(prev_rows, engine=e)
        ws.cell(row=r, column=5, value=pr if pr is not None else "-")
        ws.cell(row=r, column=6, value=f'=IF(OR(D{r}="-",E{r}="-"),"-",D{r}-E{r})')
        ws.cell(row=r, column=7, value=f'=COUNTIFS({rng("C")},A{r},{rng("J")},"是")')
        ws.cell(row=r, column=8, value=f'=COUNTIFS({rng("C")},A{r},{rng("N")},"是")')
        ws.cell(row=r, column=9, value=f'=IFERROR(AVERAGEIFS({rng("K")},{rng("C")},A{r}),"-")')
        ws.cell(row=r, column=10, value=f'=COUNTIFS({rng("C")},A{r},{rng("Q")},"<>")')
        r += 1
    first, last = 7, r - 1
    ws.cell(row=r, column=1, value="合计")
    for col in (2, 3, 7, 8, 10):
        L = get_column_letter(col)
        ws.cell(row=r, column=col, value=f"=SUM({L}{first}:{L}{last})")
    ws.cell(row=r, column=4, value=f'=IF(B{r}=0,"-",C{r}/B{r})')
    pr = _rate(prev_rows)
    ws.cell(row=r, column=5, value=pr if pr is not None else "-")
    ws.cell(row=r, column=6, value=f'=IF(OR(D{r}="-",E{r}="-"),"-",D{r}-E{r})')
    for rr in range(first, r + 1):
        for cc in range(1, len(cols) + 1):
            c = ws.cell(row=rr, column=cc)
            _body(c)
            if rr == r:
                c.font = Font(name=FONT, size=10, bold=True)
        for cc in (4, 5):
            ws.cell(row=rr, column=cc).number_format = "0.0%"
        ws.cell(row=rr, column=6).number_format = '+0.0%;-0.0%;0.0%'
        ws.cell(row=rr, column=9).number_format = "0.0"

    r += 2
    ws.cell(row=r, column=1, value="按语言").font = TITLE_FONT
    r += 1
    _header(ws, r, ["语言", "有效检测", "被提及", "提及率", "上次提及率", "变化"])
    langs = list(dict.fromkeys(x.get("language") for x in rows if x.get("language")))
    r += 1
    for lang in langs:
        ws.cell(row=r, column=1, value=lang)
        ws.cell(row=r, column=2, value=f'=COUNTIFS({rng("D")},A{r},{rng("Q")},"")')
        ws.cell(row=r, column=3, value=f'=COUNTIFS({rng("D")},A{r},{rng("I")},"是")')
        ws.cell(row=r, column=4, value=f'=IF(B{r}=0,"-",C{r}/B{r})')
        pr = _rate(prev_rows, language=lang)
        ws.cell(row=r, column=5, value=pr if pr is not None else "-")
        ws.cell(row=r, column=6, value=f'=IF(OR(D{r}="-",E{r}="-"),"-",D{r}-E{r})')
        for cc in range(1, 7):
            _body(ws.cell(row=r, column=cc))
        ws.cell(row=r, column=4).number_format = "0.0%"
        ws.cell(row=r, column=5).number_format = "0.0%"
        ws.cell(row=r, column=6).number_format = '+0.0%;-0.0%;0.0%'
        r += 1

    r += 1
    notes = [
        "说明：",
        "· 被提及 = 回答里引用了本站链接，或回答正文出现品牌名（" + "、".join(cfg.brand_terms) + "）。",
        "· 被引用 = 回答的来源链接里有本站页面；排名 = 本站在被引用的供应商网站中排第几（已排除维基、Reddit 等通用网站）。",
        "· 「上次提及率」只统计两次都检测过的问题，来自上一次检测的数据库记录；其余数字由「明细」表实时计算。",
        "· 这是通过 API 模拟提问得到的近似结果，与用户在 App 里看到的答案可能不同（模型版本、个性化、地区都会影响）。",
    ]
    for t in notes:
        ws.cell(row=r, column=1, value=t).font = NOTE_FONT
        r += 1
    widths = [18, 10, 9, 9, 11, 9, 15, 13, 10, 8]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def _changes_sheet(ws, rows, prev_rows, prev) -> None:
    ws["A1"] = "与上次相比的变化"
    ws["A1"].font = TITLE_FONT
    if not prev:
        ws["A2"] = "这是第一次检测，下次检测后会显示新增和丢失的推荐。"
        ws["A2"].font = NOTE_FONT
        ws.column_dimensions["A"].width = 60
        return
    before = {(r["question_id"], r["engine"]): r for r in prev_rows if not r.get("error")}
    gained, lost = [], []
    for r in rows:
        if r.get("error"):
            continue
        b = before.get((r["question_id"], r["engine"]))
        if not b:
            continue
        if r.get("mentioned") and not b.get("mentioned"):
            gained.append(r)
        elif b.get("mentioned") and not r.get("mentioned"):
            lost.append(r)
    _header(ws, 3, ["变化", "问题ID", "引擎", "优先级", "语言", "监测问题", "本次排名", "上次排名"])
    i = 4
    for label, items in (("新增推荐", gained), ("丢失推荐", lost)):
        for r in sorted(items, key=lambda x: (PRI_ORDER.get(x.get("priority"), 9), x["question_id"])):
            b = before[(r["question_id"], r["engine"])]
            vals = [label, r["question_id"], LABELS.get(r["engine"], r["engine"]), r.get("priority"),
                    r.get("language"), r.get("prompt"), r.get("rank"), b.get("rank")]
            for j, v in enumerate(vals, 1):
                _body(ws.cell(row=i, column=j, value=v), wrap=j == 6)
            ws.cell(row=i, column=1).fill = YES_FILL if label == "新增推荐" else NO_FILL
            i += 1
    if i == 4:
        ws.cell(row=4, column=1, value="本次与上次相比没有变化。").font = NOTE_FONT
    for j, w in enumerate([10, 8, 11, 7, 9, 55, 9, 9], 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def _missing_sheet(ws, rows) -> None:
    ws["A1"] = "所有引擎都没有推荐本站的问题（优先补内容）"
    ws["A1"].font = TITLE_FONT
    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_q[r["question_id"]].append(r)
    _header(ws, 3, ["问题ID", "优先级", "语言", "问题类型", "监测问题", "期望页面", "被推荐的竞品"])
    i = 4
    items = []
    for qid, rs in by_q.items():
        ok = [r for r in rs if not r.get("error")]
        if ok and not any(r.get("mentioned") for r in ok):
            comp = Counter(d for r in ok for d in (r.get("competitors") or [])[:5])
            items.append((rs[0], comp))
    items.sort(key=lambda x: (PRI_ORDER.get(x[0].get("priority"), 9), x[0]["question_id"]))
    for r, comp in items:
        vals = [r["question_id"], r.get("priority"), r.get("language"), r.get("category"),
                r.get("prompt"), r.get("expected_url"), "; ".join(d for d, _ in comp.most_common(5))]
        for j, v in enumerate(vals, 1):
            _body(ws.cell(row=i, column=j, value=v), wrap=j in (5, 7))
        i += 1
    if not items:
        ws.cell(row=4, column=1, value="每个问题至少有一个引擎推荐了本站。").font = NOTE_FONT
    for j, w in enumerate([8, 7, 9, 10, 52, 40, 50], 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def _competitor_sheet(ws, rows) -> None:
    ws["A1"] = "同时被 AI 推荐的其他网站（程序统计）"
    ws["A1"].font = TITLE_FONT
    count: Counter = Counter()
    questions: dict[str, set] = defaultdict(set)
    engines: dict[str, set] = defaultdict(set)
    for r in rows:
        for d in r.get("competitors") or []:
            count[d] += 1
            questions[d].add(r["question_id"])
            engines[d].add(LABELS.get(r["engine"], r["engine"]))
    _header(ws, 3, ["网站", "出现次数", "涉及问题数", "出现的引擎"])
    for i, (d, c) in enumerate(count.most_common(50), 4):
        vals = [d, c, len(questions[d]), "、".join(sorted(engines[d]))]
        for j, v in enumerate(vals, 1):
            _body(ws.cell(row=i, column=j, value=v))
    ws.cell(row=2, column=1, value="排除了维基、Reddit、社交媒体等通用网站；名单可在 .env 的 IGNORE_DOMAINS 调整。").font = NOTE_FONT
    for j, w in enumerate([36, 10, 11, 30], 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def _traffic_sheet(ws, db: DB, end: date) -> None:
    start = end - timedelta(days=7)
    prev_start = start - timedelta(days=7)
    s, e, ps = start.isoformat(), end.isoformat(), prev_start.isoformat()
    ws["A1"] = f"AI 带来的访问（Nginx 日志，{s} 至 {e}）"
    ws["A1"].font = TITLE_FONT
    cur = db.query("SELECT source, SUM(hits) AS hits FROM ai_referrals WHERE day >= %s AND day < %s "
                   "GROUP BY source", (s, e))
    pre = {x["source"]: x["hits"] for x in db.query(
        "SELECT source, SUM(hits) AS hits FROM ai_referrals WHERE day >= %s AND day < %s GROUP BY source",
        (ps, s))}
    _header(ws, 3, ["来源", "本周访问", "前一周访问", "变化"])
    i = 4
    for x in sorted(cur, key=lambda x: -int(x["hits"])):
        vals = [x["source"], int(x["hits"]), int(pre.get(x["source"]) or 0), f"=B{i}-C{i}"]
        for j, v in enumerate(vals, 1):
            _body(ws.cell(row=i, column=j, value=v))
        i += 1
    if i == 4:
        ws.cell(row=4, column=1, value="本周日志里没有来自 AI 的访问（或日志尚未接入）。").font = NOTE_FONT
        i += 1

    i += 1
    ws.cell(row=i, column=1, value="AI 访客的落地页（前 20）").font = TITLE_FONT
    i += 1
    _header(ws, i, ["页面", "来源", "访问"])
    i += 1
    pages = db.query("SELECT path, source, SUM(hits) AS hits FROM ai_referrals WHERE day >= %s AND day < %s "
                     "GROUP BY path, source ORDER BY SUM(hits) DESC LIMIT 20", (s, e))
    for p in pages:
        for j, v in enumerate([p["path"], p["source"], int(p["hits"])], 1):
            _body(ws.cell(row=i, column=j, value=v))
        i += 1

    i += 1
    ws.cell(row=i, column=1, value="AI 爬虫抓取次数").font = TITLE_FONT
    i += 1
    _header(ws, i, ["爬虫", "本周抓取", "前一周抓取"])
    i += 1
    bots = db.query("SELECT bot, SUM(hits) AS hits FROM ai_crawlers WHERE day >= %s AND day < %s "
                    "GROUP BY bot ORDER BY SUM(hits) DESC", (s, e))
    pre_b = {x["bot"]: x["hits"] for x in db.query(
        "SELECT bot, SUM(hits) AS hits FROM ai_crawlers WHERE day >= %s AND day < %s GROUP BY bot", (ps, s))}
    for b in bots:
        for j, v in enumerate([b["bot"], int(b["hits"]), int(pre_b.get(b["bot"]) or 0)], 1):
            _body(ws.cell(row=i, column=j, value=v))
        i += 1
    i += 1
    ws.cell(row=i, column=1, value=(
        "说明：Google AI 概览 / AI 模式的点击在日志里显示为普通 Google 搜索，无法单独区分；"
        "「用户提问时实时访问」类爬虫说明有人在 AI 里问到了相关问题，AI 正在读取本站页面。")).font = NOTE_FONT
    for j, w in enumerate([46, 14, 12, 10], 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def _usage_sheet(ws, cfg: Config, engines: list[str], n: int) -> None:
    D = "明细"
    rng = lambda col: f"{D}!${col}$2:${col}${max(n, 2)}"  # noqa: E731
    ws["A1"] = "本次用量与费用估算"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("蓝色单价来自 .env（美元），可直接在表里修改；未填写的单价为 0，只统计用量。"
                "实际费用以各家后台账单为准。")
    ws["A2"].font = NOTE_FONT
    _header(ws, 4, ["引擎", "调用次数", "输入 tokens", "输出 tokens", "搜索次数",
                    "输入单价/百万", "输出单价/百万", "搜索单价/千次", "估算费用 (USD)"])
    i = 5
    for e in engines:
        p_in, p_out, p_search = cfg.price.get(e, (0.0, 0.0, 0.0))
        vals = [LABELS.get(e, e),
                f'=COUNTIFS({rng("C")},A{i})',
                f'=SUMIFS({rng("R")},{rng("C")},A{i})',
                f'=SUMIFS({rng("S")},{rng("C")},A{i})',
                f'=SUMIFS({rng("T")},{rng("C")},A{i})',
                p_in, p_out, p_search,
                f"=C{i}/1000000*F{i}+D{i}/1000000*G{i}+E{i}/1000*H{i}"]
        for j, v in enumerate(vals, 1):
            c = ws.cell(row=i, column=j, value=v)
            _body(c)
            if j in (6, 7, 8):
                c.font = Font(name=FONT, size=10, color="0000FF")
                c.number_format = "$0.00"
        for j in (3, 4, 5):
            ws.cell(row=i, column=j).number_format = "#,##0"
        ws.cell(row=i, column=9).number_format = "$#,##0.00"
        i += 1
    ws.cell(row=i, column=1, value="合计")
    for col in (2, 3, 4, 5, 9):
        L = get_column_letter(col)
        c = ws.cell(row=i, column=col, value=f"=SUM({L}5:{L}{i - 1})")
        c.number_format = "$#,##0.00" if col == 9 else "#,##0"
    for col in range(1, 10):
        _body(ws.cell(row=i, column=col))
        ws.cell(row=i, column=col).font = Font(name=FONT, size=10, bold=True)
    for j, w in enumerate([12, 10, 13, 13, 10, 13, 13, 13, 14], 1):
        ws.column_dimensions[get_column_letter(j)].width = w
