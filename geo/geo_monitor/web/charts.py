"""Server-rendered SVG trend chart (no external scripts; hover handled by app.js)."""
from __future__ import annotations

from html import escape

W, H = 1000, 300
PAD_L, PAD_R, PAD_T, PAD_B = 64, 176, 20, 40
SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4"]


def trend_svg(trend: dict) -> str:
    runs, series = trend.get("runs") or [], trend.get("series") or []
    if not runs or not series:
        return ""
    n = len(runs)
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    x = (lambda i: PAD_L + pw / 2) if n == 1 else (lambda i: PAD_L + pw * i / (n - 1))
    y = lambda v: PAD_T + ph * (1 - v)  # noqa: E731

    parts = [f'<svg class="trend" viewBox="0 0 {W} {H}" role="img" aria-labelledby="trend-title" '
             f'preserveAspectRatio="xMidYMid meet">']
    # grid + y labels
    for t in (0, 0.25, 0.5, 0.75, 1.0):
        yy = y(t)
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{yy:.1f}" y2="{yy:.1f}"/>')
        parts.append(f'<text class="axis" x="{PAD_L - 8}" y="{yy + 4:.1f}" text-anchor="end">{int(t * 100)}%</text>')
    # x labels (thin out when many)
    step = max(1, (n + 5) // 6)
    for i, r in enumerate(runs):
        if i % step == 0 or i == n - 1:
            parts.append(f'<text class="axis" x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">'
                         f'{r["started_at"].strftime("%m-%d")}</text>')

    # lines, then markers, then end labels
    label_pos = []
    for si, s in enumerate(series):
        var = SERIES_VARS[si % len(SERIES_VARS)]
        segs, cur = [], []
        for i, v in enumerate(s["points"]):
            if v is None:
                if cur:
                    segs.append(cur)
                cur = []
            else:
                cur.append((x(i), y(v)))
        if cur:
            segs.append(cur)
        for seg in segs:
            if len(seg) > 1:
                d = " ".join(f"{'M' if k == 0 else 'L'}{px:.1f},{py:.1f}" for k, (px, py) in enumerate(seg))
                parts.append(f'<path class="line" d="{d}" style="stroke:var({var})"/>')
        for i, v in enumerate(s["points"]):
            if v is not None:
                parts.append(f'<circle class="dot" cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" '
                             f'style="fill:var({var})"/>')
        last = next(((i, v) for i, v in reversed(list(enumerate(s["points"]))) if v is not None), None)
        if last:
            label_pos.append([y(last[1]), s["label"], last[1], var])

    # Larger report type needs room between labels as well as at both edges.
    label_pos.sort(key=lambda t: t[0])
    for k in range(1, len(label_pos)):
        if label_pos[k][0] - label_pos[k - 1][0] < 24:
            label_pos[k][0] = label_pos[k - 1][0] + 24
    bottom = H - PAD_B
    for k in reversed(range(len(label_pos))):
        label_pos[k][0] = min(label_pos[k][0], bottom)
        bottom = label_pos[k][0] - 24
    for yy, lab, v, var in label_pos:
        parts.append(f'<text class="end-label" x="{W - PAD_R + 10}" y="{yy + 4:.1f}">'
                     f'{escape(lab)} {v:.0%}</text>')

    # hover columns: data for app.js tooltip
    colw = pw / max(n - 1, 1) if n > 1 else pw
    for i, r in enumerate(runs):
        vals = "|".join(f'{escape(s["label"])}:{"" if s["points"][i] is None else round(s["points"][i] * 100)}'
                        for s in series)
        x0 = max(PAD_L - colw / 2, x(i) - colw / 2)
        parts.append(
            f'<rect class="hover-col" x="{x0:.1f}" y="{PAD_T}" width="{colw:.1f}" height="{ph}" '
            f'data-x="{x(i):.1f}" data-date="{r["started_at"].strftime("%Y-%m-%d")}" '
            f'data-run="{r["id"]}" data-vals="{vals}"/>')
    parts.append(f'<line class="crosshair" x1="0" x2="0" y1="{PAD_T}" y2="{PAD_T + ph}"/>')
    parts.append("</svg>")
    return "".join(parts)
