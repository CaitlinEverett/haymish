"""Environment checks: permissions, library, backends. Run `haymish doctor` first.

Each check returns (ok, label, detail-or-fix). Nothing here mutates anything and
nothing triggers a permission prompt except PhotoKit status *reading* (safe).
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

import httpx

VISION_MODEL_MARKERS = ("gemma3", "qwen2.5vl", "qwen3-vl", "llava", "llama3.2-vision", "minicpm-v", "moondream")


def _host_app_hint() -> str:
    term = os.environ.get("TERM_PROGRAM", "")
    hints = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "Cursor / VS Code"}
    return hints.get(term, "the app you run haymish from (Terminal / iTerm / Cursor)")


def check_macos() -> tuple[bool, str, str]:
    ver = platform.mac_ver()[0]
    ok = bool(ver) and int(ver.split(".")[0]) >= 13
    return ok, "macOS", f"{ver or 'not macOS?'} (needs 13+; built against 26.x)"


def check_library(library: Path) -> tuple[bool, str, str]:
    if library.exists():
        return True, "Photos library", str(library)
    return False, "Photos library", f"not found at {library} — set [global].library in rules.toml"


def check_full_disk_access(library: Path) -> tuple[bool, str, str]:
    db = library / "database" / "Photos.sqlite"
    try:
        with open(db, "rb") as f:
            f.read(16)
        return True, "Full Disk Access", "can read Photos database"
    except FileNotFoundError:
        return False, "Full Disk Access", f"{db} missing — unexpected library layout"
    except PermissionError:
        return False, "Full Disk Access", (
            f"blocked. Fix: System Settings → Privacy & Security → Full Disk Access → "
            f"enable {_host_app_hint()}, then restart it."
        )


def check_photosdb(library: Path) -> tuple[bool, str, str]:
    """Only meaningful once FDA passes; smoke-loads the schema."""
    try:
        import osxphotos

        db = osxphotos.PhotosDB(dbfile=str(library))
        n = len(db.photos(intrash=False))
        return True, "osxphotos schema", f"v{osxphotos.__version__}, {n} photos readable"
    except Exception as e:  # schema churn on new macOS is the expected failure mode
        return False, "osxphotos schema", f"{type(e).__name__}: {e}"


def check_photokit_auth() -> tuple[bool, str, str]:
    try:
        import Photos

        status = Photos.PHPhotoLibrary.authorizationStatusForAccessLevel_(
            Photos.PHAccessLevelReadWrite
        )
        names = {0: "not requested yet", 1: "restricted", 2: "denied", 3: "authorized", 4: "limited"}
        label = names.get(status, str(status))
        if status == 3:
            return True, "PhotoKit access (hide/delete)", label
        if status == 0:
            return True, "PhotoKit access (hide/delete)", "not requested yet — first hide/delete will prompt"
        return False, "PhotoKit access (hide/delete)", (
            f"{label}. Fix: System Settings → Privacy & Security → Photos → allow {_host_app_hint()}"
        )
    except Exception as e:
        return False, "PhotoKit access (hide/delete)", f"pyobjc Photos framework unavailable: {e}"


def check_automation() -> tuple[bool, str, str]:
    return True, "Photos automation (albums/keywords)", (
        "checked lazily — macOS prompts on the first album/keyword write"
    )


def check_ollama(host: str, model: str) -> tuple[bool, str, str]:
    from .ai.ollama_client import available_models, model_available

    models = available_models(host)
    if not models:
        return False, "Ollama", f"not reachable at {host} — LLM rules will be skipped"
    # Exact-tag check: gemma3:27b pointing at a machine that only has gemma3:4b
    # must fail here, not 404 mid-sweep.
    if model_available(host, model):
        return True, "Ollama", f"{model} available"
    vision = sorted(m for m in models if any(v in m for v in VISION_MODEL_MARKERS))
    return False, "Ollama", (
        f"{model} not pulled. Vision-capable models present: {vision or 'none'} — "
        f"`ollama pull {model}` or point [global.ollama].model at one of those."
    )


def check_ai_index(config) -> tuple[bool, str, str]:
    """Embedding + planner models for `haymish index/find/ask` and semantic rules."""
    from .ai.ollama_client import available_models

    models = available_models(config.ollama_host)
    if not models:
        return False, "AI index (ask/find)", (
            f"Ollama not reachable at {config.ollama_host} — index/find/ask unavailable"
        )
    missing = [m for m in (config.ai_embed_model, config.ai_planner_model)
               if m not in models and m.split(":")[0] not in models]
    if missing:
        return False, "AI index (ask/find)", (
            f"missing model(s): {', '.join(missing)} — `ollama pull {missing[0]}` "
            f"(or change [global.ai] in rules.toml)"
        )
    return True, "AI index (ask/find)", (
        f"embed={config.ai_embed_model}, planner={config.ai_planner_model}, "
        f"vision={config.ai_vision_model}"
    )


def check_backup(backup: Path | None) -> tuple[bool, str, str]:
    if backup is None:
        return True, "Backup volume", (
            "not configured — archive/delete stages will refuse to run until "
            "[global].backup is set (a USB stick or external drive path works)"
        )
    if backup.exists() and os.access(backup, os.W_OK):
        free = shutil.disk_usage(backup).free // 1_000_000_000
        return True, "Backup volume", f"{backup} writable, {free} GB free"
    return False, "Backup volume", f"{backup} missing or not writable — archive/delete stages will skip"


def run_all(config=None) -> list[tuple[bool, str, str]]:
    from .paths import DEFAULT_LIBRARY

    library = config.library if config else DEFAULT_LIBRARY
    checks = [check_macos(), check_library(library)]
    fda = check_full_disk_access(library)
    checks.append(fda)
    if fda[0]:
        checks.append(check_photosdb(library))
    checks.append(check_photokit_auth())
    checks.append(check_automation())
    if config:
        checks.append(check_ollama(config.ollama_host, config.ollama_model))
        checks.append(check_ai_index(config))
        checks.append(check_backup(config.backup))
    return checks
