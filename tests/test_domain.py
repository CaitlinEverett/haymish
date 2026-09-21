"""Pure domain behavior — no Photos library or catalog access."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from haymish.config import Rule, StageConfig
from haymish.domain import (
    AutomationLevel,
    Cadence,
    CadenceKind,
    Collection,
    Disposition,
    Lens,
    LensOperator,
    Routine,
    collection_from_rule,
)


def _collection(disposition: Disposition) -> Collection:
    return Collection(
        id="screenshots",
        name="Screenshots",
        lens=Lens.atom("query", {"screenshot": True}),
        disposition=disposition,
    )


def test_canonical_lens_hash_ignores_mapping_key_order():
    first = Lens.atom(
        "query",
        {"near": {"lat": 41.88, "lon": -87.63}, "albums": ["Trips", "Chicago"]},
    )
    second = Lens.atom(
        "query",
        {"albums": ["Trips", "Chicago"], "near": {"lon": -87.63, "lat": 41.88}},
    )

    assert first.to_json() == second.to_json()
    assert first.revision == second.revision
    assert len(first.revision) == 64


def test_all_and_any_revisions_are_commutative():
    query = Lens.atom("query", screenshot=True)
    detector = Lens.atom("detector", name="receipts")

    assert Lens.all(query, detector).revision == Lens.all(detector, query).revision
    assert Lens.any(query, detector).revision == Lens.any(detector, query).revision


def test_lens_composition_validation_and_convenient_factories():
    atom = Lens.atom("query", screenshot=True)

    assert Lens.all_of(atom).operator is LensOperator.ALL
    assert Lens.any_of(atom).operator is LensOperator.ANY
    assert Lens.negate(atom).operator is LensOperator.NOT
    with pytest.raises(ValueError, match="at least one child"):
        Lens.all()
    with pytest.raises(ValueError, match="at least one child"):
        Lens.any()
    with pytest.raises(ValueError, match="exactly one child"):
        Lens(LensOperator.NOT, children=(atom, atom))
    with pytest.raises(ValueError, match="non-empty evidence source"):
        Lens.atom(" ")


def test_lens_is_deeply_immutable():
    lens = Lens.atom("query", {"near": {"lat": 1}, "albums": ["One"]})

    with pytest.raises(TypeError):
        lens.criteria["new"] = True
    with pytest.raises(TypeError):
        lens.criteria["near"]["lat"] = 2
    with pytest.raises(FrozenInstanceError):
        lens.source = "detector"


def test_disposition_deduplicates_and_sorts_tags_and_views():
    disposition = Disposition(
        keywords=(" swept:receipt ", "expense", "expense"),
        albums=("Receipts", "Archive", "Receipts"),
    )

    assert disposition.keywords == ("expense", "swept:receipt")
    assert disposition.albums == ("Archive", "Receipts")
    assert disposition.is_additive_only is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keywords": ("",)},
        {"albums": ("  ",)},
        {"hide_after_days": -1},
        {"archive_after_days": -1},
        {"stage_delete_after_days": -1},
    ],
)
def test_disposition_rejects_empty_names_and_negative_ages(kwargs):
    with pytest.raises(ValueError):
        Disposition(**kwargs)


def test_disposition_rejects_invalid_lifecycle_and_protection_conflicts():
    with pytest.raises(ValueError, match="archive must occur before"):
        Disposition(archive_after_days=30, stage_delete_after_days=30)
    with pytest.raises(ValueError, match="protect"):
        Disposition(protect=True, hide_after_days=1)
    with pytest.raises(ValueError, match="protect"):
        Disposition(protect=True, stage_delete_after_days=1)

    protected_archive = Disposition(protect=True, archive_after_days=1)
    assert protected_archive.protect
    assert not protected_archive.has_destructive_stages
    assert not protected_archive.is_additive_only


def test_disposition_hashes_are_canonical_and_target_fingerprint_ignores_timing():
    first = Disposition(
        keywords=("b", "a"),
        albums=("Two", "One"),
        hide_after_days=7,
    )
    reordered = Disposition(
        keywords=("a", "b"),
        albums=("One", "Two"),
        hide_after_days=7,
    )
    later = Disposition(
        keywords=("a", "b"),
        albums=("One", "Two"),
        hide_after_days=14,
    )

    assert first.revision == reordered.revision
    assert first.target_fingerprint == reordered.target_fingerprint
    assert first.revision != later.revision
    assert first.target_fingerprint == later.target_fingerprint


def test_collection_revision_includes_owned_domain_values():
    collection = _collection(Disposition(keywords=("swept:screenshot",)))
    same = _collection(Disposition(keywords=("swept:screenshot",)))
    changed = Collection(
        id=collection.id,
        name=collection.name,
        lens=collection.lens,
        disposition=collection.disposition,
        description="A browser-visible collection",
    )

    assert collection.revision == same.revision
    assert collection.revision != changed.revision


def test_all_automation_levels_are_explicit():
    assert {level.value for level in AutomationLevel} == {
        "explore",
        "hand-pick",
        "review-group",
        "review-new",
        "sample-then-apply",
        "trusted-additive",
        "delayed-state",
        "stage-only",
    }


def test_routine_enforces_automation_safety():
    additive = _collection(Disposition(keywords=("safe",), protect=True))
    stateful = _collection(Disposition(hide_after_days=7))
    staged = _collection(Disposition(stage_delete_after_days=30))

    assert Routine(additive, AutomationLevel.TRUSTED_ADDITIVE).automation_level is (
        AutomationLevel.TRUSTED_ADDITIVE
    )
    with pytest.raises(ValueError, match="additive-only"):
        Routine(stateful, AutomationLevel.TRUSTED_ADDITIVE)
    with pytest.raises(ValueError, match="delayed-state"):
        Routine(staged, AutomationLevel.DELAYED_STATE)
    with pytest.raises(ValueError, match="hand-pick, full review, or stage-only"):
        Routine(staged, AutomationLevel.TRUSTED_ADDITIVE)
    with pytest.raises(ValueError, match="hand-pick, full review, or stage-only"):
        Routine(staged, AutomationLevel.SAMPLE_THEN_APPLY)
    with pytest.raises(ValueError, match="stage-only requires"):
        Routine(additive, AutomationLevel.STAGE_ONLY)

    assert Routine(staged, AutomationLevel.STAGE_ONLY)
    assert Routine(staged, AutomationLevel.REVIEW_GROUP)
    assert Routine(staged, AutomationLevel.REVIEW_NEW)
    assert Routine(staged, AutomationLevel.HAND_PICK)


def test_cadence_factories_and_validation():
    assert Cadence.manual().kind is CadenceKind.MANUAL
    assert Cadence.hourly(3).interval_hours == 3
    assert Cadence.hourly(interval_hours=2).interval_hours == 2
    assert Cadence.daily(23).hour == 23
    assert Cadence.weekly(0, 8).weekday == 0
    assert Cadence.weekly(6, 8).weekday == 6

    with pytest.raises(ValueError, match="positive"):
        Cadence.hourly(0)
    with pytest.raises(ValueError, match="between 0 and 23"):
        Cadence.daily(24)
    with pytest.raises(ValueError, match="0 \\(Monday\\) through 6"):
        Cadence.weekly(7, 8)
    with pytest.raises(ValueError, match="cannot have schedule fields"):
        Cadence(CadenceKind.MANUAL, hour=8)


def test_legacy_rule_conversion_maps_evidence_and_disposition():
    rule = Rule(
        name="receipt-candidates",
        query={"keywords": ["receipt"], "screenshot": True},
        detector="receipts",
        semantic={"query": "a receipt", "min_score": 0.4},
        classify={"backend": "ollama", "prompt": "Is this a receipt?"},
        file={"keyword": "expense:receipt", "album": "Expenses/Receipts"},
        hide=StageConfig(after_days=7),
        archive=StageConfig(after_days=30),
        delete=StageConfig(after_days=90),
    )

    collection = collection_from_rule(rule)

    assert collection.id == rule.name
    assert collection.name == rule.name
    assert collection.lens.operator is LensOperator.ALL
    assert {child.source for child in collection.lens.children} == {
        "query",
        "detector",
        "semantic",
        "classify",
    }
    assert collection.disposition == Disposition(
        keywords=("expense:receipt",),
        albums=("Expenses/Receipts",),
        hide_after_days=7,
        archive_after_days=30,
        stage_delete_after_days=90,
    )
    assert Collection.from_rule(rule).revision == collection.revision


def test_legacy_rule_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="no lens evidence"):
        collection_from_rule(Rule(name="empty"))
