"""Catalog ledger + review-reject memory — uses a temp sqlite file."""

from __future__ import annotations

from pathlib import Path

from haymish.catalog import Catalog, prompt_hash


def test_review_rejects_persist_per_rule(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")
    cat.reject_candidate("u1", "screenshots-general")
    cat.reject_candidate("u1", "selfies")
    cat.reject_candidate("u2", "screenshots-general")

    assert cat.rejected_uuids_for_rule("screenshots-general") == {"u1", "u2"}
    assert cat.rejected_uuids_for_rule("selfies") == {"u1"}
    assert cat.rejected_uuids_for_rule("dupes") == set()
    cat.close()


def test_verdict_cache_roundtrips_detail(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")
    ph = prompt_hash("ollama", "gemma3:27b", "is this an ad?")
    cat.put_verdict("u1", "ad-screenshots", "ollama", ph, True, 0.91, "shopping page")

    hit = cat.get_verdict("u1", "ad-screenshots", ph)
    assert hit == (True, 0.91, "shopping page")

    miss = cat.get_verdict("u1", "ad-screenshots", prompt_hash("ollama", "gemma3:27b", "different"))
    assert miss is None
    cat.close()


def test_last_undoable_run_prefers_newest_apply(tmp_path: Path):
    cat = Catalog(tmp_path / "catalog.db")
    cat.start_run("scan")
    sweep_id = cat.start_run("sweep-apply")
    cat.finish_run(sweep_id, {})
    review_id = cat.start_run("review-apply")
    cat.finish_run(review_id, {})
    cat.start_run("sweep-dry-run")

    assert cat.last_undoable_run_id() == review_id
    cat.close()
