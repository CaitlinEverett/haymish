"""Installs/removes a launchd LaunchAgent that runs `haymish scheduled-run` on a
schedule — that command indexes (so new photos get captioned/embedded and
find/ask/semantic rules keep seeing them) then runs `sweep --apply`, in one
unattended pass.

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
not something scheduler.py should do on its own. `haymish scheduled-run` — the
command this module schedules — is bound by the same invariant: index + sweep
only, never confirm-deletes.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from . import hardware
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


def should_defer(skip_on_battery: bool = True) -> str | None:
    """Why a scheduled run should skip this cycle, or None if now is fine.

    Thin delegate to hardware.busy_reason() so the scheduled-run command has one
    obvious place to ask, and so the policy lives with the hardware probing.
    """
    return hardware.busy_reason(skip_on_battery=skip_on_battery)


def _program_arguments(refresh_index: bool = True, defer_when_busy: bool = True) -> list[str]:
    """The argv launchd runs: always `haymish scheduled-run`, which does its own
    load gating and runs index + sweep in-process (never confirm-deletes)."""
    haymish = _haymish_invocation()
    cmd = f"{haymish} scheduled-run"
    if not refresh_index:
        cmd += " --no-index"
    if not defer_when_busy:
        cmd += " --force"
    # launchd's ProgramArguments takes one argv, not a shell pipeline -- going
    # through a login shell keeps PATH/quoting consistent with an interactive run.
    return [shutil.which("zsh") or "/bin/sh", "-lc", cmd]


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, check=False, text=True)


def _bootout_existing() -> None:
    uid = os.getuid()
    result = _launchctl("bootout", f"gui/{uid}/{PLIST_LABEL}")
    if result.returncode != 0:
        _launchctl("unload", str(PLIST_PATH))


def install(interval_hours: int = 24, refresh_index: bool = True,
            at_hour: int | None = None, defer_when_busy: bool = True) -> None:
    """Install (or reinstall) the LaunchAgent.

    With at_hour set, the job uses launchd's StartCalendarInterval and runs once
    a day at that hour instead of on a rolling StartInterval. Note that launchd
    jobs do not fire while the Mac is asleep: a calendar job whose time passed
    during sleep runs once on wake, which is exactly what we want for an
    overnight index+sweep on a machine that may or may not be awake at 3am.
    (StartInterval behaves similarly, but its phase drifts with every reboot,
    so a "run at night" request should always use at_hour.)
    """
    if at_hour is not None and not 0 <= at_hour <= 23:
        raise ValueError(f"at_hour must be between 0 and 23, got {at_hour}")

    ensure_app_dirs()
    APP_DIR.mkdir(exist_ok=True)

    if PLIST_PATH.exists():
        _bootout_existing()

    plist = {
        "Label": PLIST_LABEL,
        "ProgramArguments": _program_arguments(refresh_index=refresh_index,
                                                defer_when_busy=defer_when_busy),
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }
    if at_hour is not None:
        plist["StartCalendarInterval"] = {"Hour": at_hour, "Minute": 0}
    else:
        plist["StartInterval"] = interval_hours * 3600

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


def _read_plist() -> dict | None:
    if not PLIST_PATH.exists():
        return None
    try:
        with PLIST_PATH.open("rb") as f:
            return plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def describe_schedule(plist: dict | None) -> str:
    """Human-readable summary of when the installed job runs."""
    if not plist:
        return "not installed"
    calendar = plist.get("StartCalendarInterval")
    if isinstance(calendar, dict):
        hour = calendar.get("Hour")
        minute = calendar.get("Minute", 0) or 0
        if hour is not None:
            return f"daily at {int(hour)}:{int(minute):02d}"
        return "on a calendar schedule"
    interval = plist.get("StartInterval")
    if isinstance(interval, int):
        if interval % 3600 == 0:
            return f"every {interval // 3600}h"
        return f"every {interval}s"
    return "unknown schedule"


def status() -> dict:
    installed = PLIST_PATH.exists()

    result = _launchctl("list", PLIST_LABEL)
    loaded = result.returncode == 0

    log_tail = ""
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text(errors="replace").splitlines()
        log_tail = "\n".join(lines[-20:])

    plist = _read_plist()
    command = " ".join(plist.get("ProgramArguments", [])[-1:]) if plist else ""

    return {
        "installed": installed,
        "loaded": loaded,
        "schedule": describe_schedule(plist),
        "command": command,
        "refreshes_index": "--no-index" not in command,
        "defers_when_busy": "--force" not in command,
        "log_tail": log_tail,
    }
