"""Environment checks: permissions, library, backends. Run `haymish doctor` first.

Each check returns (ok, label, detail-or-fix). Nothing here mutates anything and
nothing triggers a permission prompt except PhotoKit status *reading* (safe).
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

VISION_MODEL_MARKERS = (
    "gemma3", "qwen2.5vl", "qwen3-vl", "llava", "llama3.2-vision", "minicpm-v", "moondream",
)

_BUNDLE_HINTS = {
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm",
    "com.todesktop.230313mzl4w4u92": "Cursor",
    "com.microsoft.VSCode": "VS Code",
}


def host_app_hint() -> str:
    """Human label for the host that launched haymish (for TCC settings paths)."""
    bundle = os.environ.get("__CFBundleIdentifier", "")
    if bundle in _BUNDLE_HINTS:
        return _BUNDLE_HINTS[bundle]
    if bundle:
        return bundle
    term = os.environ.get("TERM_PROGRAM", "")
    hints = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "Cursor / VS Code"}
    return hints.get(term, "the app you run haymish from (Terminal / iTerm / Cursor)")


def photokit_access_fix_hint() -> str:
    return (
        f"System Settings → Privacy & Security → Photos → allow Full Access for "
        f"{host_app_hint()}, then quit and reopen that app"
    )


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
            f"enable {host_app_hint()}, then restart it."
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
        host = host_app_hint()
        if status == 3:
            return True, "PhotoKit access (hide/delete)", f"{label} ({host})"
        if status == 0:
            return True, "PhotoKit access (hide/delete)", (
                f"not requested yet for {host} — first hide/delete will prompt "
                f"(prefer Terminal.app if Cursor/IDE already shows Denied)"
            )
        if status == 4:
            return False, "PhotoKit access (hide/delete)", (
                f"limited — hide/delete need Full Access. Fix: {photokit_access_fix_hint()}"
            )
        return False, "PhotoKit access (hide/delete)", (
            f"{label} for {host}. Fix: {photokit_access_fix_hint()}"
        )
    except Exception as e:
        return False, "PhotoKit access (hide/delete)", f"pyobjc Photos framework unavailable: {e}"


def check_automation() -> tuple[bool, str, str]:
    return True, "Photos automation (albums/keywords)", (
        "checked lazily — macOS prompts on the first album/keyword write"
    )


def check_ollama(host: str, model: str) -> tuple[bool, str, str]:
    from .ai.ollama_client import available_models, model_available
    from .ai.model_resolve import resolve_model

    models = available_models(host)
    if not models:
        return False, "Ollama (classify)", f"not reachable at {host} — LLM rules will be skipped"
    if model_available(host, model):
        return True, "Ollama (classify)", f"{model} available"
    resolved = resolve_model(host, model, "classify")
    vision = sorted(m for m in models if any(v in m for v in VISION_MODEL_MARKERS))
    if model_available(host, resolved.model):
        return False, "Ollama (classify)", (
            f"{model} not pulled; runtime will fall back to {resolved.model}. "
            f"Vision models present: {vision or 'none'} — "
            f"`ollama pull {model}` or point [global.ollama].model at one of those."
        )
    return False, "Ollama (classify)", (
        f"{model} not pulled. Vision-capable models present: {vision or 'none'} — "
        f"`ollama pull {model}` or point [global.ollama].model at one of those."
    )


def check_ai_index(config) -> tuple[bool, str, str]:
    """Embedding + planner + vision models for `haymish index/find/ask`."""
    from .ai.ollama_client import available_models, model_available
    from .ai.model_resolve import resolve_model

    models = available_models(config.ollama_host)
    if not models:
        return False, "AI index (ask/find)", (
            f"Ollama not reachable at {config.ollama_host} — index/find/ask unavailable"
        )

    roles = [
        ("embed", config.ai_embed_model, "embed"),
        ("planner", config.ai_planner_model, "planner"),
        ("vision", config.ai_vision_model, "caption"),
    ]
    parts = []
    missing_hard = []
    for label, preferred, role in roles:
        if model_available(config.ollama_host, preferred):
            parts.append(f"{label}={preferred}")
            continue
        resolved = resolve_model(config.ollama_host, preferred, role)  # type: ignore[arg-type]
        if model_available(config.ollama_host, resolved.model):
            parts.append(f"{label}={preferred}→{resolved.model}")
        else:
            missing_hard.append(preferred)
            parts.append(f"{label}={preferred} (missing)")

    if missing_hard:
        return False, "AI index (ask/find)", (
            f"missing model(s): {', '.join(missing_hard)} — `ollama pull {missing_hard[0]}` "
            f"(or change [global.ai] in rules.toml). Roles: {', '.join(parts)}"
        )
    return True, "AI index (ask/find)", ", ".join(parts)


def check_hardware() -> tuple[bool, str, str]:
    """Informational: what this Mac is, and how wide indexing will run on it."""
    from .hardware import detect, recommended_caption_workers

    hw = detect()
    return True, "Hardware", f"{hw.describe()} — captioning {recommended_caption_workers(hw)} at a time"


def check_index_freshness(config) -> tuple[bool, str, str]:
    """Captions written by a vision model you no longer use are stale; also report
    caption vs embedding coverage for the current keys."""
    from .catalog import Catalog
    from .ai.indexer import caption_key

    current = caption_key(config)
    try:
        catalog = Catalog()
    except Exception as e:
        return False, "Index freshness", f"can't open the catalog: {type(e).__name__}: {e}"
    try:
        models = catalog.caption_models()
        caption_n = models.get(current, 0)
        embed_n = len(catalog.embedded_uuids(config.ai_embed_model))
    finally:
        catalog.close()

    stale = {m: n for m, n in models.items() if m != current}
    if stale:
        total = sum(stale.values())
        which = ", ".join(m for m, _ in sorted(stale.items(), key=lambda kv: -kv[1]))
        return False, "Index freshness", (
            f"{total:,} caption(s) from {which} but you're now configured for {current} "
            f"— run `haymish doctor --fix index` or `haymish index --catch-up-captions`"
        )
    if not models and embed_n == 0:
        return True, "Index freshness", f"no captions yet — run `haymish index` (vision model {current})"
    if embed_n and caption_n < max(1, int(embed_n * 0.1)):
        return False, "Index freshness", (
            f"{caption_n:,} caption(s) under {current} vs {embed_n:,} embeddings — "
            f"search works but subgroups/`find` About text are thin. "
            f"Run `haymish index --catch-up-captions`"
        )
    if not models:
        return True, "Index freshness", (
            f"0 captions / {embed_n:,} embeddings — OCR-only index; "
            f"run `haymish index --catch-up-captions` for vision descriptions"
        )
    return True, "Index freshness", (
        f"{caption_n:,} caption(s) from {current}; {embed_n:,} embeddings "
        f"({config.ai_embed_model})"
    )


def check_backup(backup: Path | None, config=None) -> tuple[bool, str, str]:
    needs_backup = False
    if config is not None:
        needs_backup = any(
            r.enabled and (r.archive or r.delete) for r in config.rules
        )
    if backup is None:
        if needs_backup:
            return False, "Backup volume", (
                "not configured but archive/delete rules are enabled — set "
                "[global].backup in rules.toml (USB stick path is fine) or disable those stages"
            )
        return True, "Backup volume", (
            "not configured — archive/delete stages will refuse to run until "
            "[global].backup is set (a USB stick or external drive path works)"
        )
    if backup.exists() and os.access(backup, os.W_OK):
        free = shutil.disk_usage(backup).free // 1_000_000_000
        return True, "Backup volume", f"{backup} writable, {free} GB free"
    return False, "Backup volume", f"{backup} missing or not writable — archive/delete stages will skip"


def propose_config_fixes(config) -> list[tuple[str, str, str]]:
    """(toml_key, current_value, proposed_value) for fixable config issues.

    Pure diagnostic: proposes patches the user can paste into rules.toml.
    Never writes anything itself — the CLI prints the patch, and the human
    decides whether to apply it.
    """
    from .ai.model_resolve import resolve_model
    from .ai.ollama_client import available_models, model_available

    fixes: list[tuple[str, str, str]] = []

    # Soft safety: archive/delete without a backup path is fail-closed at
    # doctor time; propose commenting stages out (never invent a backup path).
    if not config.backup:
        for rule in config.rules or []:
            if getattr(rule, "archive", None) is not None:
                fixes.append((
                    f"[rule.{rule.name}].archive",
                    f"after_days={rule.archive.after_days}",
                    "# comment out until [global].backup is set",
                ))
            if getattr(rule, "delete", None) is not None:
                fixes.append((
                    f"[rule.{rule.name}].delete",
                    f"after_days={rule.delete.after_days}",
                    "# comment out until [global].backup is set",
                ))

    models = available_models(config.ollama_host)
    if not models:
        return fixes

    if not model_available(config.ollama_host, config.ollama_model):
        resolved = resolve_model(config.ollama_host, config.ollama_model, "classify")
        if model_available(config.ollama_host, resolved.model):
            fixes.append(("[global.ollama].model", config.ollama_model, resolved.model))

    role_map: list[tuple[str, str, str]] = [
        ("ai_embed_model", "embed", "[global.ai].embed_model"),
        ("ai_vision_model", "caption", "[global.ai].vision_model"),
        ("ai_planner_model", "planner", "[global.ai].planner_model"),
    ]
    for attr, role, toml_key in role_map:
        preferred = getattr(config, attr)
        if not model_available(config.ollama_host, preferred):
            resolved = resolve_model(config.ollama_host, preferred, role)
            if model_available(config.ollama_host, resolved.model):
                fixes.append((toml_key, preferred, resolved.model))

    return fixes


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
    checks.append(check_hardware())
    if config:
        checks.append(check_ollama(config.ollama_host, config.ollama_model))
        checks.append(check_ai_index(config))
        checks.append(check_index_freshness(config))
        checks.append(check_backup(config.backup, config))
    return checks
