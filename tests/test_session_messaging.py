"""Server session messaging: empty_state, job error surfacing, session payloads.

Tests the API-level contracts for session build messaging hooks — the signals
the dashboard uses to show "nothing to do", "all rejected", or "errors".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from haymish.config import Config, Rule, StageConfig
from haymish.server import (
    ServeState,
    _hide_preview_json,
    _review_empty_state,
    _rule_action_label,
    _session_payload,
    collections_payload,
)
from haymish.sweep import HidePreviewMeta, PreviewCandidate, RulePreview


def _config():
    return Config(
        library=Path("/nonexistent/Test.photoslibrary"),
        backup=None,
        report_dir=Path("/nonexistent/reports"),
        ollama_host="",
        ollama_model="",
        claude_model="",
        ai_embed_model="",
        ai_vision_model="",
        ai_planner_backend="ollama",
        ai_planner_model="",
        rules=[],
        source_path=Path("/nonexistent/rules.toml"),
    )


# -- _review_empty_state tests -----------------------------------------------

def test_empty_state_none_when_candidates_exist():
    rp = RulePreview(
        rule=Rule(name="r"),
        candidates=[SimpleNamespace(uuid="a")],
        preview_candidates=[PreviewCandidate(uuid="a", filename="a.jpg", date="")],
    )
    assert _review_empty_state([rp]) is None


def test_empty_state_errors_when_only_errors():
    rp = RulePreview(
        rule=Rule(name="r"),
        candidates=[],
        preview_candidates=[],
        errors=["no photos indexed yet"],
    )
    assert _review_empty_state([rp]) == "errors"


def test_empty_state_zero_matches_when_no_candidates_no_errors():
    rp = RulePreview(
        rule=Rule(name="r"),
        candidates=[],
        preview_candidates=[],
    )
    assert _review_empty_state([rp]) == "zero_matches"


def test_empty_state_handles_mixed_previews():
    """With one rule having candidates and another with errors, should be None."""
    rp1 = RulePreview(
        rule=Rule(name="a"),
        candidates=[SimpleNamespace(uuid="x")],
        preview_candidates=[PreviewCandidate(uuid="x", filename="x.jpg", date="")],
    )
    rp2 = RulePreview(
        rule=Rule(name="b"),
        candidates=[],
        preview_candidates=[],
        errors=["something went wrong"],
    )
    assert _review_empty_state([rp1, rp2]) is None


# -- _hide_preview_json tests ------------------------------------------------

def test_hide_preview_json_none():
    assert _hide_preview_json(None) is None


def test_hide_preview_json_full():
    meta = HidePreviewMeta(due=10, hideable=5, skipped_icloud=3, already_hidden=2)
    result = _hide_preview_json(meta)
    assert result == {
        "due": 10,
        "hideable": 5,
        "skipped_icloud": 3,
        "already_hidden": 2,
    }


# -- _rule_action_label tests ------------------------------------------------

def test_action_label_file_only():
    rule = Rule(name="r", file={"album": "Test"})
    assert "file" in _rule_action_label(rule).lower() or "Test" in _rule_action_label(rule)


def test_action_label_hide():
    rule = Rule(name="r", hide=StageConfig(after_days=7))
    assert "hide" in _rule_action_label(rule).lower()
    assert "7" in _rule_action_label(rule)


def test_action_label_report_only():
    rule = Rule(name="r")
    assert _rule_action_label(rule) == "report only"


def test_action_label_combined():
    rule = Rule(
        name="r",
        file={"album": "X", "keyword": "y"},
        hide=StageConfig(after_days=7),
        archive=StageConfig(after_days=30),
        delete=StageConfig(after_days=90),
    )
    label = _rule_action_label(rule)
    assert "file" in label.lower() or "X" in label
    assert "hide" in label.lower()
    assert "archive" in label.lower()
    assert "delete" in label.lower() or "stage" in label.lower()


# -- _session_payload tests --------------------------------------------------

def test_session_payload_includes_empty_state():
    rp = RulePreview(
        rule=Rule(name="r"),
        candidates=[],
        preview_candidates=[],
    )
    session = {"previews": [rp], "empty_state": "zero_matches"}
    payload = _session_payload("sess1", session)
    assert payload["session"] == "sess1"
    assert payload["empty_state"] == "zero_matches"


def test_session_payload_includes_errors():
    rp = RulePreview(
        rule=Rule(name="r"),
        candidates=[],
        preview_candidates=[],
        errors=["no photos indexed yet — run haymish index"],
    )
    session = {"previews": [rp], "empty_state": "errors"}
    payload = _session_payload("sess2", session)
    assert payload["rules"][0]["errors"] == ["no photos indexed yet — run haymish index"]
    assert payload["empty_state"] == "errors"


def test_session_payload_includes_hide_preview():
    meta = HidePreviewMeta(due=3, hideable=2, skipped_icloud=1, already_hidden=0)
    rp = RulePreview(
        rule=Rule(name="r", hide=StageConfig(after_days=7)),
        candidates=[SimpleNamespace(uuid="a")],
        preview_candidates=[PreviewCandidate(uuid="a", filename="a.jpg", date="")],
        hide_preview=meta,
    )
    session = {"previews": [rp], "empty_state": None}
    payload = _session_payload("sess3", session)
    assert payload["rules"][0]["hide"] == {
        "due": 3, "hideable": 2, "skipped_icloud": 1, "already_hidden": 0,
    }


def test_session_payload_includes_subgroups():
    rp = RulePreview(
        rule=Rule(name="r"),
        candidates=[SimpleNamespace(uuid="a")],
        preview_candidates=[PreviewCandidate(uuid="a", filename="a.jpg", date="")],
    )
    session = {
        "previews": [rp],
        "empty_state": None,
        "subgroups": {"r": [{"key": "g1", "label": "group1", "size": 5, "uuids": ["a"]}]},
    }
    payload = _session_payload("sess4", session)
    assert payload["rules"][0]["subgroups"] == [
        {"key": "g1", "label": "group1", "size": 5, "uuids": ["a"]}
    ]


def test_session_payload_candidate_fields():
    """Each candidate in the payload must have uuid, filename, date, detail, thumb."""
    rp = RulePreview(
        rule=Rule(name="r"),
        candidates=[SimpleNamespace(uuid="abc")],
        preview_candidates=[
            PreviewCandidate(uuid="abc", filename="photo.jpg", date="2026-01-01",
                             classify_detail="semantic match 0.85"),
        ],
    )
    session = {"previews": [rp], "empty_state": None}
    payload = _session_payload("sess5", session)
    candidate = payload["rules"][0]["candidates"][0]
    assert candidate["uuid"] == "abc"
    assert candidate["filename"] == "photo.jpg"
    assert candidate["date"] == "2026-01-01"
    assert candidate["detail"] == "semantic match 0.85"
    assert "thumb" in candidate  # bool


# -- collections_payload tests -----------------------------------------------

def test_collections_payload_honors_overrides():
    config = _config()
    rule = Rule(name="test-rule", enabled=True, file={"album": "X"})
    config.rules = [rule]
    # The override disables the rule
    entries = collections_payload(config, overrides={"test-rule": False})
    assert len(entries) == 1
    assert entries[0]["enabled"] is False


def test_collections_payload_survives_domain_error():
    """A rule that the domain model cannot express gets an error, not dropped."""
    config = _config()
    rule = Rule(name="broken-rule")  # no lens evidence
    config.rules = [rule]

    entries = collections_payload(config)
    assert len(entries) == 1
    assert entries[0]["error"] is not None
    assert entries[0]["name"] == "broken-rule"
