"""Hide eligibility partitioning and sweep outcome counters."""

from __future__ import annotations

from types import SimpleNamespace

from haymish.library import photo_is_hideable
from haymish.sweep import HidePreviewMeta, partition_hide_due, hide_preview_for_rule
from haymish.config import Rule, StageConfig


def _photo(uuid: str, *, hidden=False, ismissing=False):
    return SimpleNamespace(uuid=uuid, hidden=hidden, ismissing=ismissing)


def test_photo_is_hideable():
    assert photo_is_hideable(_photo("a"))
    assert not photo_is_hideable(_photo("b", hidden=True))
    assert not photo_is_hideable(_photo("c", ismissing=True))
    assert not photo_is_hideable(_photo("d", hidden=True, ismissing=True))


def test_partition_hide_due_counts():
    candidates = [
        _photo("young"),
        _photo("ok"),
        _photo("hidden", hidden=True),
        _photo("icloud", ismissing=True),
    ]
    ages = {"young": 1, "ok": 10, "hidden": 10, "icloud": 10}
    hideable, meta = partition_hide_due(candidates, ages, after_days=7)
    assert [p.uuid for p in hideable] == ["ok"]
    assert meta == HidePreviewMeta(due=3, hideable=1, skipped_icloud=1, already_hidden=1)


def test_apply_hide_stage_dry_run_sets_counters(monkeypatch, tmp_path):
    from haymish import sweep as sweep_mod
    from haymish.catalog import Catalog

    rule = Rule(name="t", hide=StageConfig(after_days=0))
    candidates = [_photo("a"), _photo("b", ismissing=True)]
    ages = {"a": 0, "b": 0}
    outcome = sweep_mod.RuleOutcome(rule="t")
    catalog = Catalog(tmp_path / "catalog.db")
    try:
        monkeypatch.setattr(sweep_mod.hide_action, "hide_photos", lambda uuids: {})
        sweep_mod._apply_hide_stage(rule, candidates, ages, "run", catalog, False, outcome)
    finally:
        catalog.close()
    assert outcome.hidden == 1
    assert outcome.hide_skipped_icloud == 1


def test_hide_preview_for_rule_none_without_hide():
    rule = Rule(name="n")
    assert hide_preview_for_rule(rule, [_photo("x")], {"x": 0}) is None


def test_hide_preview_meta_counts_all_skip_categories():
    """The meta must report all four counters so the review UI can show a complete banner."""
    candidates = [
        _photo("local-young"),
        _photo("local-ok"),
        _photo("local-ok2"),
        _photo("hidden1", hidden=True),
        _photo("hidden2", hidden=True),
        _photo("icloud1", ismissing=True),
        _photo("icloud2", ismissing=True),
        _photo("icloud3", ismissing=True),
    ]
    ages = {p.uuid: 30 for p in candidates}  # all age-eligible
    ages["local-young"] = 1  # not yet due

    rule = Rule(name="full-counts", hide=StageConfig(after_days=7))
    meta = hide_preview_for_rule(rule, candidates, ages)

    assert meta is not None
    # due = 7 (everyone except local-young at age 1)
    assert meta.due == 7
    # hideable = 2 (local-ok, local-ok2 — not hidden, not iCloud)
    assert meta.hideable == 2
    assert meta.already_hidden == 2
    assert meta.skipped_icloud == 3


def test_sweep_report_outcome_tracks_icloud_skips(monkeypatch, tmp_path):
    """RuleOutcome.hide_skipped_icloud is set even in apply mode."""
    from haymish import sweep as sweep_mod
    from haymish.catalog import Catalog

    rule = Rule(name="counter-test", hide=StageConfig(after_days=0))
    candidates = [
        _photo("ok1"),
        _photo("ok2"),
        _photo("ic1", ismissing=True),
        _photo("ic2", ismissing=True),
        _photo("hid", hidden=True),
    ]
    ages = {p.uuid: 10 for p in candidates}
    outcome = sweep_mod.RuleOutcome(rule="counter-test")
    catalog = Catalog(tmp_path / "catalog.db")
    try:
        monkeypatch.setattr(sweep_mod.hide_action, "hide_photos",
                            lambda uuids: {u: "ok" for u in uuids})
        sweep_mod._apply_hide_stage(rule, candidates, ages, "run", catalog, True, outcome)
    finally:
        catalog.close()

    assert outcome.hidden == 2  # ok1, ok2
    assert outcome.hide_skipped_icloud == 2  # ic1, ic2
    # already_hidden is not tracked in outcome (it's in HidePreviewMeta)
    # but action_errors should mention the iCloud skip
    assert any("iCloud" in e for e in outcome.action_errors)
