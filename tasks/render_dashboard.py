"""Genera ``.tasks.html`` (panel de avance) a partir de ``.tasks.md`` y ``.ticktets.md``.

Herramienta de seguimiento del proyecto (no es código de producto): solo biblioteca estándar.
Uso: ``uv run python tasks/render_dashboard.py``. El estado de cada tarea lo marca Claude Code
en ``.tasks.md``; este script no lo cambia.
"""

import html
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / ".tasks.md"
TICKETS = ROOT / ".ticktets.md"
OUTPUT = ROOT / ".tasks.html"

STATUS = {"[x]": "done", "[o]": "doing", "[ ]": "todo"}
LABELS = {"done": "Finalizadas", "doing": "En marcha", "todo": "Pendientes"}


def rows(path: Path, columns: int) -> list[list[str]]:
    """Filas de datos de las tablas Markdown con ``columns`` celdas."""
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == columns:
            found.append(cells)
    return found


def tasks() -> list[dict[str, str]]:
    result = []
    for cells in rows(TASKS, 5):
        if cells[3] in STATUS:
            result.append(
                {
                    "id": cells[0],
                    "phase": cells[1],
                    "title": cells[2],
                    "status": STATUS[cells[3]],
                    "comment": cells[4],
                }
            )
    return result


def suite() -> list[tuple[str, str, int]]:
    return [
        (cells[0], cells[1], int(cells[2]))
        for cells in rows(TASKS, 3)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]) and cells[2].isdigit()
    ]


def tickets() -> Counter[str]:
    return Counter(cells[3] for cells in rows(TICKETS, 4) if cells[0].startswith("TK-"))


def bar(done: int, doing: int, total: int) -> str:
    def pct(value: int) -> float:
        return 100.0 * value / total if total else 0.0

    return (
        '<div class="bar">'
        f'<span class="done" style="width:{pct(done):.1f}%"></span>'
        f'<span class="doing" style="width:{pct(doing):.1f}%"></span></div>'
    )


def sparkline(points: list[tuple[str, str, int]]) -> str:
    if len(points) < 2:  # noqa: PLR2004
        return ""
    width, height, pad = 640, 180, 28
    low, high = 0, max(count for _, _, count in points)
    step = (width - 2 * pad) / (len(points) - 1)
    coordinates = [
        (pad + i * step, height - pad - (count - low) / (high - low) * (height - 2 * pad))
        for i, (_, _, count) in enumerate(points)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates)
    marks = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5">'
        f"<title>{html.escape(label)}: {count}</title></circle>"
        f'<text x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle">{count}</text>'
        for (x, y), (_, label, count) in zip(coordinates, points, strict=True)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Pruebas por hito">'
        f'<polyline points="{line}" fill="none" stroke-width="2"/>{marks}</svg>'
    )


def render() -> str:
    items = tasks()
    delivered = [t for t in items if t["id"].startswith("T-")]
    counts = Counter(t["status"] for t in delivered)
    total = len(delivered)
    by_phase: dict[str, Counter[str]] = defaultdict(Counter)
    by_ticket: dict[str, Counter[str]] = defaultdict(Counter)
    for task in delivered:
        by_phase[task["phase"]][task["status"]] += 1
        by_ticket[task["id"].split(".")[0]][task["status"]] += 1
    phases = [t for t in items if not t["id"].startswith("T-")]
    series = suite()
    open_tickets = tickets()

    phase_rows = "".join(
        f"<tr><td>{html.escape(phase)}</td><td>{c['done']}/{sum(c.values())}</td>"
        f"<td>{bar(c['done'], c['doing'], sum(c.values()))}</td></tr>"
        for phase, c in by_phase.items()
    )
    ticket_rows = "".join(
        f"<tr><td>{html.escape(ticket)}</td><td>{c['done']}/{sum(c.values())}</td>"
        f"<td>{bar(c['done'], c['doing'], sum(c.values()))}</td></tr>"
        for ticket, c in by_ticket.items()
    )
    pending = "".join(
        f"<tr><td>{html.escape(t['id'])}</td><td>{html.escape(t['title'])}</td>"
        f"<td>{html.escape(t['comment'])}</td></tr>"
        for t in items
        if t["status"] != "done"
    )
    roadmap = "".join(
        f'<li class="{t["status"]}"><b>{html.escape(t["id"])}</b> {html.escape(t["title"])}</li>'
        for t in phases
    )
    cards = "".join(
        f'<div class="card {key}"><div class="n">{counts[key]}</div><div>{label}</div></div>'
        for key, label in LABELS.items()
    )
    tickets_text = " · ".join(f"{state}: {n}" for state, n in sorted(open_tickets.items()))
    return TEMPLATE.format(
        cards=cards,
        total=total,
        progress=bar(counts["done"], counts["doing"], total),
        phase_rows=phase_rows,
        ticket_rows=ticket_rows,
        spark=sparkline(series),
        pending=pending,
        roadmap=roadmap,
        tickets=html.escape(tickets_text or "sin tickets"),
        tickets_total=sum(open_tickets.values()),
    )


TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Avance — Veritas</title>
<style>
:root {{ --bg:#f6f7f9; --fg:#1b1f24; --muted:#5b6672; --card:#fff; --line:#d8dde3;
  --done:#2e8b57; --doing:#d9a21b; --todo:#9aa4af; --accent:#2f6fdc; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#14181d; --fg:#e6e9ed; --muted:#9aa4af; --card:#1c2128; --line:#2d343c;
    --done:#4cc38a; --doing:#e6b84a; --todo:#6b7682; --accent:#6ea0ff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg);
  font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:1.5rem; margin:0 0 4px; }}
h2 {{ font-size:1.05rem; margin:0 0 10px; }}
p.note {{ color:var(--muted); margin:0 0 16px; }}
.grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); }}
.panel {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }}
.cards {{ display:grid; gap:10px; grid-template-columns:repeat(3, 1fr); margin-bottom:12px; }}
.card {{ border:1px solid var(--line); border-radius:8px; padding:10px; text-align:center; }}
.card .n {{ font-size:1.8rem; font-weight:700; }}
.card.done .n {{ color:var(--done); }} .card.doing .n {{ color:var(--doing); }}
.card.todo .n {{ color:var(--todo); }}
table {{ width:100%; border-collapse:collapse; }}
td {{ padding:5px 6px; border-top:1px solid var(--line); vertical-align:middle; }}
.bar {{ display:flex; height:10px; background:var(--line); border-radius:6px; overflow:hidden;
  min-width:90px; }}
.bar .done {{ background:var(--done); }} .bar .doing {{ background:var(--doing); }}
svg {{ width:100%; height:auto; }} svg polyline {{ stroke:var(--accent); }}
svg circle {{ fill:var(--accent); }} svg text {{ fill:var(--muted); font-size:11px; }}
ul.road {{ list-style:none; margin:0; padding:0; columns:2 220px; }}
ul.road li {{ padding:3px 0 3px 18px; position:relative; }}
ul.road li::before {{ content:""; position:absolute; left:0; top:9px; width:9px; height:9px;
  border-radius:50%; background:var(--todo); }}
ul.road li.done::before {{ background:var(--done); }}
ul.road li.doing::before {{ background:var(--doing); }}
.wide {{ grid-column:1 / -1; }}
td:nth-child(3) {{ overflow-wrap:anywhere; }}
</style>
</head>
<body>
<main>
<h1>Avance del desarrollo — Veritas Structural Calculation 3D</h1>
<p class="note">Generado desde <code>.tasks.md</code> y <code>.ticktets.md</code>. Aplicación
experimental: no apta para justificar obras reales sin validación independiente.</p>
<div class="grid">
<section class="panel wide"><h2>Entregas ({total} registradas)</h2>
<div class="cards">{cards}</div>{progress}</section>
<section class="panel"><h2>Por fase</h2><table>{phase_rows}</table></section>
<section class="panel"><h2>Por ticket</h2><table>{ticket_rows}</table></section>
<section class="panel wide"><h2>Pruebas automáticas por hito</h2>{spark}</section>
<section class="panel"><h2>Fases del roadmap</h2><ul class="road">{roadmap}</ul></section>
<section class="panel"><h2>Tickets de pendientes ({tickets_total})</h2><p>{tickets}</p></section>
<section class="panel wide"><h2>Tareas no finalizadas</h2><table>{pending}</table></section>
</div>
</main>
</body>
</html>
"""


if __name__ == "__main__":
    OUTPUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"escrito {OUTPUT.name}")
