"""Menu-bar status/trigger surface for Haymish.

Deliberately decoupled from the rules engine: talks to the rest of Haymish only
via the installed `haymish` CLI (subprocess) and read-only sqlite reads against
catalog.db. Never imports haymish.sweep or actions/* -- those evolve on a
different timeline and this module must stay independently buildable.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from pathlib import Path

import rumps

from .notify import escape_applescript_string
from .paths import CATALOG_PATH, DEFAULT_REPORT_DIR

# Computed, not hardcoded -- a hardcoded absolute path here only worked on the
# original checkout it was authored on; any other machine, account, or moved/renamed
# clone would silently no-op the Confirm Deletes menu item (its "cd" would fail and
# the trailing "&& haymish confirm-deletes" would then never run).
PROJECT_DIR = str(Path(__file__).resolve().parent.parent)
STAGED_DELETES_POLL_SECONDS = 60


def _catalog_ro_connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{CATALOG_PATH}?mode=ro", uri=True)


def _read_last_run() -> dict | None:
    try:
        con = _catalog_ro_connect()
    except sqlite3.OperationalError:
        return None
    try:
        row = con.execute(
            "SELECT started, finished, mode, stats FROM runs ORDER BY started DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    if row is None:
        return None
    started, finished, mode, stats_json = row
    try:
        stats = json.loads(stats_json) if stats_json else {}
    except (TypeError, ValueError):
        stats = {}
    return {"started": started, "finished": finished, "mode": mode, "stats": stats}


def _read_staged_delete_count() -> int:
    try:
        con = _catalog_ro_connect()
    except sqlite3.OperationalError:
        return 0
    try:
        row = con.execute("SELECT COUNT(*) FROM staged_deletes").fetchone()
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()
    return row[0] if row else 0


def _format_status(run: dict | None) -> str:
    if run is None:
        return "No sweeps run yet"
    when = run.get("finished") or run.get("started") or "unknown time"
    counts = run.get("stats", {}).get("counts", {})
    if counts:
        parts = ", ".join(f"{name}: {n}" for name, n in counts.items())
        return f"Last {run.get('mode', 'run')}: {when} ({parts})"
    return f"Last {run.get('mode', 'run')}: {when}"


class HaymishMenuBarApp(rumps.App):
    def __init__(self):
        super().__init__("Haymish", title="🧹", quit_button="Quit")
        self._sweep_running = False

        self.status_item = rumps.MenuItem("Loading status…")
        self.status_item.set_callback(None)
        self.confirm_deletes_item = rumps.MenuItem("Confirm Deletes (0)")

        self.menu = [
            self.status_item,
            None,
            "Open Haymish",
            "Review Now",
            "Sweep Now (no review)",
            self.confirm_deletes_item,
            "Open Last Report",
            None,
        ]

        self.refresh_status()
        self.staged_deletes_timer = rumps.Timer(
            self._on_staged_deletes_timer, STAGED_DELETES_POLL_SECONDS
        )
        self.staged_deletes_timer.start()

    def refresh_status(self):
        self.status_item.title = _format_status(_read_last_run())
        self.refresh_staged_deletes_count()

    def refresh_staged_deletes_count(self):
        self.confirm_deletes_item.title = f"Confirm Deletes ({_read_staged_delete_count()})"

    def _on_staged_deletes_timer(self, _timer):
        self.refresh_staged_deletes_count()

    @rumps.clicked("Open Haymish")
    def open_haymish(self, sender):
        # `haymish app` handles daemon startup + browser open; run detached so a
        # slow first library load never blocks the menu bar's run loop.
        subprocess.Popen(["haymish", "app"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True)

    @rumps.clicked("Review Now")
    def review_now(self, _sender):
        """Opens the thumbnail review UI in a Terminal window so you can confirm
        before anything is applied — the safe day-to-day dogfood path."""
        shell_cmd = escape_applescript_string(f"cd {PROJECT_DIR} && uv run haymish review")
        script = (
            'tell application "Terminal"\n'
            "  activate\n"
            f'  do script "{shell_cmd}"\n'
            "end tell"
        )
        subprocess.run(["osascript", "-e", script])

    @rumps.clicked("Sweep Now (no review)")
    def sweep_now(self, sender):
        if self._sweep_running:
            return
        self._sweep_running = True
        sender.title = "Sweeping…"
        sender.set_callback(None)
        thread = threading.Thread(target=self._run_sweep, args=(sender,), daemon=True)
        thread.start()

    def _run_sweep(self, sender):
        from .notify import notify

        try:
            proc = subprocess.run(
                ["uv", "run", "haymish", "sweep", "--apply"],
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if proc.returncode == 0:
                summary = (proc.stdout or "").strip()[-200:] or "Done."
                notify("Sweep complete", summary)
            else:
                tail = (proc.stderr or "").strip().splitlines()
                detail = "\n".join(tail[-5:]) or f"exit code {proc.returncode}"
                notify("Sweep failed", detail)
        except Exception as e:
            notify("Sweep failed", str(e))
        finally:
            self._sweep_running = False
            sender.title = "Sweep Now (no review)"
            sender.set_callback(self.sweep_now)
            self.refresh_status()

    @rumps.clicked("Confirm Deletes (0)")
    def confirm_deletes(self, sender):
        shell_cmd = escape_applescript_string(f"cd {PROJECT_DIR} && uv run haymish confirm-deletes")
        script = (
            'tell application "Terminal"\n'
            "  activate\n"
            f'  do script "{shell_cmd}"\n'
            "end tell"
        )
        subprocess.run(["osascript", "-e", script])

    @rumps.clicked("Open Last Report")
    def open_last_report(self, sender):
        reports = sorted(
            DEFAULT_REPORT_DIR.glob("*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not reports:
            rumps.notification("Haymish", "No report found", f"Nothing in {DEFAULT_REPORT_DIR}")
            return
        subprocess.run(["open", str(reports[0])])


def main():
    HaymishMenuBarApp().run()


if __name__ == "__main__":
    main()
