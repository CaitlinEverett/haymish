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
