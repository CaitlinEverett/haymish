"""Scan/sweep reports: rich console summary + markdown + self-contained HTML."""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .types import DetectorResult

MAX_LISTED = 40


def console_summary(console: Console, library_stats: dict, results: list[DetectorResult]) -> None:
    table = Table(title="Haymish scan", show_lines=False)
    table.add_column("Detector", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Detail")
    for r in results:
        detail = ", ".join(f"{k}={v}" for k, v in r.stats.items()) if r.stats else ""
        table.add_row(r.title, str(r.count), detail)
    console.print(table)
    lib = ", ".join(f"{k}: {v}" for k, v in library_stats.items())
    console.print(f"[dim]Library — {lib}[/dim]")
    for r in results:
        for note in r.notes:
            console.print(f"[yellow]note ({r.name}):[/yellow] {note}")


def _md(library_stats: dict, results: list[DetectorResult], generated: str) -> str:
    lines = [f"# Haymish scan — {generated}", "", "## Library", ""]
    lines += [f"- **{k}**: {v}" for k, v in library_stats.items()]
    for r in results:
        lines += ["", f"## {r.title} — {r.count}", ""]
        for k, v in r.stats.items():
            lines.append(f"- {k}: {v}")
        for note in r.notes:
            lines.append(f"- ⚠️ {note}")
        if r.candidates:
            lines.append("")
            for c in r.candidates[:MAX_LISTED]:
                date = c.date.strftime("%Y-%m-%d") if c.date else "?"
                lines.append(f"- `{c.filename}` ({date}) — {c.reason}")
            if r.count > MAX_LISTED:
                lines.append(f"- … and {r.count - MAX_LISTED} more")
    return "\n".join(lines) + "\n"


def _html(md_body: str, generated: str) -> str:
    body = html.escape(md_body)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Haymish scan {generated}</title>
<style>
body {{ font: 15px/1.5 -apple-system, sans-serif; max-width: 860px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
pre {{ white-space: pre-wrap; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #1c1c1e; color: #eee; }} }}
</style></head>
<body><pre>{body}</pre></body></html>
"""


def write_reports(report_dir: Path, library_stats: dict, results: list[DetectorResult]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    md = _md(library_stats, results, generated)
    md_path = report_dir / f"scan-{stamp}.md"
    md_path.write_text(md)
    (report_dir / f"scan-{stamp}.html").write_text(_html(md, generated))
    return md_path
