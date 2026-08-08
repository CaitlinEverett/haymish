"""Installs/removes a launchd LaunchAgent that runs `haymish index` then
`haymish sweep --apply` on a schedule, so new photos landing since the last run
get captioned/embedded (so find/ask/semantic rules keep seeing them) and swept
in one pass, unattended.

SAFETY INVARIANT: scheduling this to run unattended is safe by construction.
Per this codebase's design (see haymish/actions/delete.py), the "delete"
lifecycle stage never deletes anything itself — it only stages candidates via
Catalog.stage_delete(). Turning a staged delete into an actual deletion
requires a separate, human-run `haymish confirm-deletes` command, which shows
a typed confirmation prompt AND macOS's own un-bypassable system dialog.
There is no unattended-deletion path anywhere in this codebase. Indexing is
likewise inert with respect to the library — it only reads photos and writes
to the local catalog. This module must never add a path to `confirm-deletes`
(or anything downstream of it) from the scheduled job, ever. If that
invariant ever needs to change, it's a deliberate decision made elsewhere,
not something scheduler.py should do on its own.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from .paths import APP_DIR, ensure_app_dirs

PLIST_LABEL = "com.haymish.sweep"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / (PLIST_LABEL + ".plist")
LOG_PATH = Path.home() / ".haymish" / "scheduler.log"

_PROJECT_DIR = Path(__file__).resolve().parent.parent


def _haymish_invocation() -> str:
    """A shell-quoted command that runs `haymish`, preferring the installed
    binary and falling back to `uv run` from this checkout."""
    haymish_bin = shutil.which("haymish")
    if haymish_bin:
        return f'"{haymish_bin}"'

    uv_bin = shutil.which("uv")
    if uv_bin:
        return f'"{uv_bin}" run --project "{_PROJECT_DIR}" haymish'

    raise RuntimeError(
        "can't find `haymish` or `uv` on PATH — install one of them (or activate "
        "the project's venv) before running `haymish schedule`"
    )


def _program_arguments(refresh_index: bool = True) -> list[str]:
    haymish = _haymish_invocation()
    sweep_cmd = f"{haymish} sweep --apply"
    if not refresh_index:
        # launchd's ProgramArguments takes one argv, not a shell pipeline -- a
        # single command still needs to go through a shell to expand quoting
        # consistently with the chained case below, so this stays uniform.
        return [shutil.which("zsh") or "/bin/sh", "-lc", sweep_cmd]
    index_cmd = f"{haymish} index"
    return [shutil.which("zsh") or "/bin/sh", "-lc", f"{index_cmd} && {sweep_cmd}"]


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, check=False, text=True)


def _bootout_existing() -> None:
    uid = os.getuid()
    result = _launchctl("bootout", f"gui/{uid}/{PLIST_LABEL}")
    if result.returncode != 0:
        _launchctl("unload", str(PLIST_PATH))


def install(interval_hours: int = 24, refresh_index: bool = True) -> None:
    ensure_app_dirs()
    APP_DIR.mkdir(exist_ok=True)

    if PLIST_PATH.exists():
        _bootout_existing()

    plist = {
        "Label": PLIST_LABEL,
        "ProgramArguments": _program_arguments(refresh_index=refresh_index),
        "StartInterval": interval_hours * 3600,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as f:
        plistlib.dump(plist, f)

    uid = os.getuid()
    result = _launchctl("bootstrap", f"gui/{uid}", str(PLIST_PATH))
    if result.returncode != 0:
        _launchctl("load", str(PLIST_PATH))


def uninstall() -> None:
    if PLIST_PATH.exists():
        _bootout_existing()
    else:
        uid = os.getuid()
        _launchctl("bootout", f"gui/{uid}/{PLIST_LABEL}")

    PLIST_PATH.unlink(missing_ok=True)


def status() -> dict:
    installed = PLIST_PATH.exists()

    result = _launchctl("list", PLIST_LABEL)
    loaded = result.returncode == 0

    log_tail = ""
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text(errors="replace").splitlines()
        log_tail = "\n".join(lines[-20:])

    return {"installed": installed, "loaded": loaded, "log_tail": log_tail}
