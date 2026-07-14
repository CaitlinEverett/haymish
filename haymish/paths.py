"""Filesystem locations for Haymish state."""

from pathlib import Path

APP_DIR = Path.home() / ".haymish"
RULES_PATH = APP_DIR / "rules.toml"
CATALOG_PATH = APP_DIR / "catalog.db"
ACTION_LOG_PATH = APP_DIR / "actions.jsonl"
DEFAULT_REPORT_DIR = APP_DIR / "reports"

DEFAULT_LIBRARY = Path.home() / "Pictures" / "Photos Library.photoslibrary"


def ensure_app_dirs() -> None:
    APP_DIR.mkdir(exist_ok=True)
    DEFAULT_REPORT_DIR.mkdir(exist_ok=True)
