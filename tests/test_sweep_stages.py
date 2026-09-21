"""Pure sweep helpers — age gates and confirmed-apply reject bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from haymish.catalog import Catalog
from haymish.config import Rule, StageConfig
from haymish.sweep import PreviewCandidate, RulePreview, _due, apply_confirmed


@dataclass
class FakePhoto:
    uuid: str
    original_filename: str = "x.jpg"


def test_due_filters_by_photo_age():
    photos = [FakePhoto("a"), FakePhoto("b"), FakePhoto("c")]
    ages = {"a": 5, "b": 14, "c": 30}
    assert [p.uuid for p in _due(photos, ages, 14)] == ["b", "c"]
    assert [p.uuid for p in _due(photos, ages, 31)] == []


def test_apply_confirmed_records_rejects(tmp_path: Path):
    """Unchecked photos are remembered; stage helpers see only the checked set."""
    cat = Catalog(tmp_path / "catalog.db")
    rule = Rule(
        name="screenshots-general",
        query={"screenshot": True},
        file={"album": "Swept/Screenshots"},
        hide=StageConfig(after_days=30),
    )
    keep = FakePhoto("keep")
    skip = FakePhoto("skip")
    preview = RulePreview(
        rule=rule,
        candidates=[keep, skip],
        preview_candidates=[
            PreviewCandidate(uuid="keep", filename="keep.jpg", date=""),
            PreviewCandidate(uuid="skip", filename="skip.jpg", date=""),
        ],
    )
    config = SimpleNamespace(backup=None)
    seen: list[list] = []

    def _capture_file(rule, candidates, run_id, catalog, apply, outcome):
        seen.append(list(candidates))
        outcome.filed = len(candidates)

    with (
        patch("haymish.sweep._apply_file_stage", side_effect=_capture_file),
        patch("haymish.sweep._apply_hide_stage"),
        patch("haymish.sweep._apply_archive_stage"),
        patch("haymish.sweep._apply_delete_stage"),
    ):
        report = apply_confirmed(config, cat, [preview], {"screenshots-general": {"keep"}})

    assert cat.rejected_uuids_for_rule("screenshots-general") == {"skip"}
    assert report.apply is True
    assert report.outcomes[0].matched == 1
    assert report.outcomes[0].filed == 1
    assert [p.uuid for p in seen[0]] == ["keep"]
    cat.close()


def test_apply_confirmed_rejects_are_durable_across_sessions(tmp_path: Path):
    """Rejected uuids persist in the catalog and are queryable later."""
    cat = Catalog(tmp_path / "catalog.db")
    rule = Rule(name="test-rule", file={"album": "Test"})
    preview = RulePreview(
        rule=rule,
        candidates=[FakePhoto("a"), FakePhoto("b"), FakePhoto("c")],
        preview_candidates=[
            PreviewCandidate(uuid="a", filename="a.jpg", date=""),
            PreviewCandidate(uuid="b", filename="b.jpg", date=""),
            PreviewCandidate(uuid="c", filename="c.jpg", date=""),
        ],
    )

    with (
        patch("haymish.sweep._apply_file_stage"),
        patch("haymish.sweep._apply_hide_stage"),
        patch("haymish.sweep._apply_archive_stage"),
        patch("haymish.sweep._apply_delete_stage"),
    ):
        # Keep only "a", reject "b" and "c"
        apply_confirmed(SimpleNamespace(backup=None), cat, [preview], {"test-rule": {"a"}})

    # Verify the rejects are queryable
    rejected = cat.rejected_uuids_for_rule("test-rule")
    assert rejected == {"b", "c"}

    # Re-open the catalog to verify durability
    cat.close()
    cat2 = Catalog(tmp_path / "catalog.db")
    assert cat2.rejected_uuids_for_rule("test-rule") == {"b", "c"}
    cat2.close()


def test_apply_confirmed_empty_selection_rejects_all(tmp_path: Path):
    """If no uuids are selected for a rule, all candidates are rejected."""
    cat = Catalog(tmp_path / "catalog.db")
    rule = Rule(name="empty-sel", file={"album": "X"})
    preview = RulePreview(
        rule=rule,
        candidates=[FakePhoto("x"), FakePhoto("y")],
        preview_candidates=[
            PreviewCandidate(uuid="x", filename="x.jpg", date=""),
            PreviewCandidate(uuid="y", filename="y.jpg", date=""),
        ],
    )

    with (
        patch("haymish.sweep._apply_file_stage"),
        patch("haymish.sweep._apply_hide_stage"),
        patch("haymish.sweep._apply_archive_stage"),
        patch("haymish.sweep._apply_delete_stage"),
    ):
        report = apply_confirmed(SimpleNamespace(backup=None), cat, [preview], {"empty-sel": set()})

    assert cat.rejected_uuids_for_rule("empty-sel") == {"x", "y"}
    assert report.outcomes[0].matched == 0
    assert report.outcomes[0].rejected == 2
    cat.close()


def test_apply_confirmed_tracks_rejection_count(tmp_path: Path):
    """The outcome.rejected count matches the unchecked candidates."""
    cat = Catalog(tmp_path / "catalog.db")
    rule = Rule(name="rej-count", file={"album": "X"})
    photos = [FakePhoto(f"p{i}") for i in range(5)]
    preview = RulePreview(
        rule=rule,
        candidates=photos,
        preview_candidates=[
            PreviewCandidate(uuid=p.uuid, filename=f"{p.uuid}.jpg", date="")
            for p in photos
        ],
    )

    with (
        patch("haymish.sweep._apply_file_stage"),
        patch("haymish.sweep._apply_hide_stage"),
        patch("haymish.sweep._apply_archive_stage"),
        patch("haymish.sweep._apply_delete_stage"),
    ):
        # Keep 2, reject 3
        report = apply_confirmed(
            SimpleNamespace(backup=None), cat, [preview],
            {"rej-count": {"p0", "p1"}}
        )

    assert report.outcomes[0].matched == 2
    assert report.outcomes[0].rejected == 3
    cat.close()
