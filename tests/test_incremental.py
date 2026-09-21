"""Incremental catalog behavior, isolated to temporary SQLite databases."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from haymish.catalog import Catalog
from haymish.incremental import EffectState, ObservationResult, ProcessorState


def test_observe_asset_reports_new_changed_and_unchanged(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")

    with patch(
        "haymish.catalog._now",
        side_effect=[
            "2026-09-02T14:00:00+00:00",
            "2026-09-02T14:01:00+00:00",
            "2026-09-02T14:02:00+00:00",
        ],
    ):
        assert cat.observe_asset("photos-a", "u1", "fp-1") is ObservationResult.NEW
        assert cat.observe_asset("photos-a", "u1", "fp-1") is ObservationResult.UNCHANGED
        assert cat.observe_asset("photos-a", "u1", "fp-2") is ObservationResult.CHANGED

    row = cat.db.execute(
        "SELECT fingerprint, first_seen_at, last_seen_at, changed_at "
        "FROM observed_assets WHERE library_id=? AND uuid=?",
        ("photos-a", "u1"),
    ).fetchone()
    assert row == (
        "fp-2",
        "2026-09-02T14:00:00+00:00",
        "2026-09-02T14:02:00+00:00",
        "2026-09-02T14:02:00+00:00",
    )
    cat.close()


def test_incremental_records_are_isolated_by_library(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")

    assert cat.observe_asset("photos-a", "same-uuid", "fp-a") == "new"
    assert cat.observe_asset("photos-b", "same-uuid", "fp-b") == "new"
    assert cat.observe_asset("photos-a", "same-uuid", "fp-a") == "unchanged"
    assert cat.observe_asset("photos-b", "same-uuid", "fp-b") == "unchanged"

    cat.record_processor_state(
        "photos-a", "same-uuid", "caption", "v1", "fp-a", ProcessorState.COMPLETED
    )
    assert not cat.processor_needs_work(
        "photos-a", "same-uuid", "caption", "v1", "fp-a"
    )
    assert cat.processor_needs_work(
        "photos-b", "same-uuid", "caption", "v1", "fp-a"
    )

    cat.record_effect(
        "photos-a", "same-uuid", "album:receipts", "1", "target-a", EffectState.APPLIED
    )
    assert cat.effect_is_satisfied(
        "photos-a", "same-uuid", "album:receipts", "1", "target-a"
    )
    assert not cat.effect_is_satisfied(
        "photos-b", "same-uuid", "album:receipts", "1", "target-a"
    )
    cat.close()


def test_processor_version_input_and_failure_invalidate_work(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")
    key = ("photos-a", "u1", "caption")

    assert cat.processor_needs_work(*key, "v1", "input-1")
    cat.record_processor_state(*key, "v1", "input-1", "running")
    assert cat.processor_needs_work(*key, "v1", "input-1")

    cat.record_processor_state(*key, "v1", "input-1", "completed")
    cat.record_processor_state(*key, "v1", "input-1", "completed")
    assert not cat.processor_needs_work(*key, "v1", "input-1")
    assert cat.processor_needs_work(*key, "v2", "input-1")
    assert cat.processor_needs_work(*key, "v1", "input-2")

    cat.record_processor_state(*key, "v2", "input-1", "failed", error="backend down")
    assert cat.processor_needs_work(*key, "v2", "input-1")
    assert cat.db.execute(
        "SELECT state, error FROM processor_states "
        "WHERE library_id=? AND uuid=? AND processor_id=? "
        "AND processor_version=? AND input_fingerprint=?",
        (*key, "v2", "input-1"),
    ).fetchone() == ("failed", "backend down")

    cat.record_processor_state(*key, "v2", "input-1", "completed")
    assert not cat.processor_needs_work(*key, "v2", "input-1")
    # Recording v2 must not erase the exact reusable v1 result.
    assert not cat.processor_needs_work(*key, "v1", "input-1")
    assert cat.db.execute("SELECT COUNT(*) FROM processor_states").fetchone()[0] == 2

    with pytest.raises(ValueError):
        cat.record_processor_state(*key, "v2", "input-1", "unknown")
    cat.close()


def test_completed_processor_identity_is_terminal_and_keeps_its_timestamp(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")
    key = ("photos-a", "u1", "caption", "v1", "input-1")

    with patch(
        "haymish.catalog._now",
        side_effect=["2026-09-02T14:00:00+00:00", "2026-09-02T14:01:00+00:00"],
    ):
        cat.record_processor_state(*key, "completed")
        # A late/stale worker may report running after another worker completed.
        # It must not regress the durable terminal result.
        cat.record_processor_state(*key, "running", error="stale error")

    row = cat.db.execute(
        "SELECT state, error, created_at, updated_at FROM processor_states"
    ).fetchone()
    assert row == (
        "completed", None,
        "2026-09-02T14:00:00+00:00", "2026-09-02T14:00:00+00:00",
    )
    cat.close()


def test_schema_migration_rolls_back_if_a_failure_follows_drop(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")
    cat.db.execute("CREATE TABLE migration_source(value TEXT NOT NULL)")
    cat.db.execute("INSERT INTO migration_source VALUES('durable')")
    cat.db.commit()

    with pytest.raises(sqlite3.OperationalError):
        cat._run_schema_migration("""
            CREATE TABLE migration_copy(value TEXT NOT NULL);
            INSERT INTO migration_copy SELECT value FROM migration_source;
            DROP TABLE migration_source;
            SELECT definitely_not_a_column;
        """)

    assert cat.db.execute("SELECT value FROM migration_source").fetchone() == ("durable",)
    assert cat.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='migration_copy'"
    ).fetchone() is None
    cat.close()


def test_legacy_processor_key_migrates_without_losing_completed_work(tmp_path: Path):
    path = tmp_path / "catalog.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE processor_states("
        "library_id TEXT NOT NULL, uuid TEXT NOT NULL, processor_id TEXT NOT NULL, "
        "processor_version TEXT NOT NULL, input_fingerprint TEXT NOT NULL, "
        "state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "PRIMARY KEY(library_id, uuid, processor_id))"
    )
    legacy.execute(
        "INSERT INTO processor_states VALUES(?,?,?,?,?,?,?,?,?)",
        ("photos-a", "u1", "caption", "v1", "input-1", "completed", None, "t1", "t2"),
    )
    legacy.commit()
    legacy.close()

    cat = Catalog(path)

    columns = cat.db.execute("PRAGMA table_info(processor_states)").fetchall()
    primary_key = [
        name for _, name in sorted((column[5], column[1]) for column in columns if column[5])
    ]
    assert primary_key == [
        "library_id", "uuid", "processor_id", "processor_version", "input_fingerprint"
    ]
    assert not cat.processor_needs_work("photos-a", "u1", "caption", "v1", "input-1")
    assert cat.db.execute(
        "SELECT state, created_at, updated_at FROM processor_states"
    ).fetchone() == ("completed", "t1", "t2")
    cat.close()


def test_effect_revision_target_and_state_control_satisfaction(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")
    key = ("photos-a", "u1", "album:receipts")

    assert not cat.effect_is_satisfied(*key, "1", "target-1")
    cat.record_effect(*key, "1", "target-1", "pending")
    assert not cat.effect_is_satisfied(*key, "1", "target-1")

    cat.record_effect(*key, "1", "target-1", "applied")
    cat.record_effect(*key, "1", "target-1", "applied")
    assert cat.effect_is_satisfied(*key, "1", "target-1")
    assert not cat.effect_is_satisfied(*key, "2", "target-1")
    assert not cat.effect_is_satisfied(*key, "1", "target-2")

    cat.record_effect(*key, "2", "target-1", "verified")
    assert cat.effect_is_satisfied(*key, "2", "target-1")
    cat.record_effect(*key, "1", "target-2", "failed", error="album unavailable")
    assert not cat.effect_is_satisfied(*key, "1", "target-2")
    assert cat.db.execute(
        "SELECT error FROM applied_effects "
        "WHERE library_id=? AND uuid=? AND disposition_id=? "
        "AND disposition_revision=? AND target_fingerprint=?",
        (*key, "1", "target-2"),
    ).fetchone()[0] == "album unavailable"
    assert cat.db.execute("SELECT COUNT(*) FROM applied_effects").fetchone()[0] == 3

    with pytest.raises(ValueError):
        cat.record_effect(*key, "1", "target-1", "unknown")
    cat.close()


def test_incremental_state_persists_across_reopen(tmp_path: Path):
    path = tmp_path / "catalog.db"
    cat = Catalog(path)
    cat.observe_asset("photos-a", "u1", "fp-1")
    cat.record_processor_state(
        "photos-a", "u1", "caption", "v1", "fp-1", ProcessorState.COMPLETED
    )
    cat.record_effect(
        "photos-a", "u1", "album:receipts", "1", "target-1", EffectState.VERIFIED
    )
    cat.close()

    reopened = Catalog(path)
    assert reopened.observe_asset("photos-a", "u1", "fp-1") is ObservationResult.UNCHANGED
    assert not reopened.processor_needs_work(
        "photos-a", "u1", "caption", "v1", "fp-1"
    )
    assert reopened.effect_is_satisfied(
        "photos-a", "u1", "album:receipts", "1", "target-1"
    )
    reopened.close()


def test_every_incremental_primary_key_includes_library_id(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")
    for table in ("observed_assets", "processor_states", "applied_effects"):
        columns = cat.db.execute(f"PRAGMA table_info({table})").fetchall()
        primary_key = [column[1] for column in columns if column[5]]
        assert "library_id" in primary_key

    processor_columns = cat.db.execute("PRAGMA table_info(processor_states)").fetchall()
    processor_key = [
        name for _, name in sorted(
            (column[5], column[1]) for column in processor_columns if column[5]
        )
    ]
    assert processor_key == [
        "library_id", "uuid", "processor_id", "processor_version", "input_fingerprint"
    ]
    cat.close()
