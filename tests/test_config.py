"""Config parse + safety invariants — no Photos library needed."""

from __future__ import annotations

from pathlib import Path

import pytest

from haymish.config import ConfigError, load_config


def _write(tmp: Path, body: str) -> Path:
    path = tmp / "rules.toml"
    path.write_text(body)
    return path


def _load(tmp: Path, body: str):
    return load_config(_write(tmp, body), packs_dir=tmp / "packs")


def test_load_starter_template(tmp_path: Path):
    template = Path(__file__).resolve().parents[1] / "haymish" / "rules-template.toml"
    cfg = _load(tmp_path, template.read_text())
    assert cfg.rule("screenshots-general").file == {
        "album": "Swept/Screenshots", "keyword": "swept:screenshot"
    }
    assert cfg.rule("junk").report_only is True
    assert all(rule.hide is None for rule in cfg.rules)
    assert all(rule.archive is None for rule in cfg.rules)
    assert all(rule.delete is None for rule in cfg.rules)
    assert cfg.backup is None


def test_delete_without_archive_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="delete stage but no archive"):
        _load(
            tmp_path,
            """
[global]
[rule.bad]
query = { screenshot = true }
file = { album = "X" }
delete = { after_days = 90 }
""",
        )


def test_archive_must_precede_delete(tmp_path: Path):
    with pytest.raises(ConfigError, match="must be less than"):
        _load(
            tmp_path,
            """
[global]
[rule.bad]
query = { screenshot = true }
archive = { after_days = 90 }
delete = { after_days = 90 }
""",
        )


def test_unknown_exclude_matched_by(tmp_path: Path):
    with pytest.raises(ConfigError, match="unknown rule"):
        _load(
            tmp_path,
            """
[global]
[rule.a]
query = { screenshot = true }
exclude_matched_by = ["nope"]
""",
        )


def test_unknown_query_key(tmp_path: Path):
    with pytest.raises(ConfigError, match="unknown keys"):
        _load(
            tmp_path,
            """
[global]
[rule.a]
query = { not_a_real_key = true }
""",
        )
