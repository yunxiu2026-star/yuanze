"""Load monitoring questions from the Excel question list."""
from __future__ import annotations

from dataclasses import dataclass

from openpyxl import load_workbook

SHEET = "监测问题"
COLUMNS = {
    "id": "ID",
    "language": "语言",
    "market": "目标市场",
    "category": "问题类型",
    "prompt": "监测问题 (Prompt)",
    "meaning": "中文释义",
    "priority": "优先级",
    "expected_url": "期望被引用页面",
    "enabled": "启用",
}


@dataclass
class Question:
    id: str
    language: str
    market: str
    category: str
    prompt: str
    meaning: str
    priority: str
    expected_url: str
    enabled: bool


def load_questions(path: str, priorities: list[str] | None = None,
                   only_enabled: bool = True) -> list[Question]:
    wb = load_workbook(path, read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        raise ValueError(f"问题清单里找不到工作表「{SHEET}」: {path}")
    rows = wb[SHEET].iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    missing = [v for v in COLUMNS.values() if v not in header]
    if missing:
        raise ValueError(f"问题清单缺少列: {', '.join(missing)}")
    idx = {k: header.index(v) for k, v in COLUMNS.items()}

    out: list[Question] = []
    seen: set[str] = set()
    for row in rows:
        def cell(key: str) -> str:
            val = row[idx[key]] if idx[key] < len(row) else None
            return "" if val is None else str(val).strip()

        qid, prompt = cell("id"), cell("prompt")
        if not qid or not prompt:
            continue
        if qid in seen:
            raise ValueError(f"问题清单里 ID 重复: {qid}")
        seen.add(qid)
        q = Question(
            id=qid, language=cell("language"), market=cell("market"),
            category=cell("category"), prompt=prompt, meaning=cell("meaning"),
            priority=cell("priority").upper() or "P3", expected_url=cell("expected_url"),
            enabled=cell("enabled") not in ("否", "no", "No", "N", "0", "false"),
        )
        if only_enabled and not q.enabled:
            continue
        if priorities and q.priority not in [p.upper() for p in priorities]:
            continue
        out.append(q)
    wb.close()
    return out
