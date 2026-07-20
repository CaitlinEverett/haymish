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
