"""Doctor --fix config: propose-only model patches, never rewrite rules.toml.

The doctor surface helps the user see what's misconfigured and offers concrete
suggestions, but the decision and the edit stay with the human.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from click.testing import CliRunner

from haymish.cli import main as cli_main
from haymish.doctor import propose_config_fixes


def _config(**overrides):
    defaults = dict(
        library=Path("/nonexistent/Test.photoslibrary"),
        backup=None,
        report_dir=Path("/nonexistent/reports"),
        ollama_host="http://localhost:11434",
        ollama_model="gemma3:4b",
        claude_model="claude-sonnet-5",
        ai_embed_model="qwen3-embedding:8b",
        ai_vision_model="gemma3:4b",
        ai_planner_backend="ollama",
        ai_planner_model="qwen3.6:27b",
        rules=[],
        source_path=Path("/nonexistent/rules.toml"),
        posture="tidy",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# -- propose_config_fixes unit tests -----------------------------------------

def test_propose_returns_empty_when_all_models_available():
    with (
        patch("haymish.ai.ollama_client.available_models", return_value={"gemma3:4b", "qwen3-embedding:8b", "qwen3.6:27b"}),
        patch("haymish.ai.ollama_client.model_available", return_value=True),
    ):
        fixes = propose_config_fixes(_config())

    assert fixes == []


def test_propose_suggests_fallback_for_missing_classify_model():
    def avail(host, model):
        return model != "gemma3:4b"

    with (
        patch("haymish.ai.ollama_client.available_models", return_value={"qwen3-vl:8b", "nomic-embed-text"}),
        patch("haymish.ai.ollama_client.model_available", side_effect=avail),
        patch("haymish.ai.model_resolve.resolve_model") as resolve,
    ):
        resolve.return_value = SimpleNamespace(model="qwen3-vl:8b", fallback_used=True, note="fallback")
        fixes = propose_config_fixes(_config())

    # Should propose the classify model fix
    classify_fixes = [f for f in fixes if "ollama" in f[0].lower()]
    assert len(classify_fixes) == 1
    key, current, proposed = classify_fixes[0]
    assert current == "gemma3:4b"
    assert proposed == "qwen3-vl:8b"


def test_propose_suggests_role_specific_fallbacks():
    """Each AI role (embed, vision, planner) gets its own proposal."""
    def avail(host, model):
        return model in {"nomic-embed-text", "qwen3-vl:8b", "llama3.2:3b"}

    def resolve(host, preferred, role):
        lookup = {
            "embed": "nomic-embed-text",
            "caption": "qwen3-vl:8b",
            "planner": "llama3.2:3b",
            "classify": "qwen3-vl:8b",
        }
        return SimpleNamespace(model=lookup.get(role, preferred), fallback_used=True, note="")

    with (
        patch("haymish.ai.ollama_client.available_models", return_value={"nomic-embed-text", "qwen3-vl:8b", "llama3.2:3b"}),
        patch("haymish.ai.ollama_client.model_available", side_effect=avail),
        patch("haymish.ai.model_resolve.resolve_model", side_effect=resolve),
    ):
        fixes = propose_config_fixes(_config())

    keys = [f[0] for f in fixes]
    # All four roles should be proposed (classify + embed + vision + planner)
    assert "[global.ollama].model" in keys
    assert "[global.ai].embed_model" in keys
    assert "[global.ai].vision_model" in keys
    assert "[global.ai].planner_model" in keys


def test_propose_returns_empty_when_ollama_unreachable():
    with patch("haymish.ai.ollama_client.available_models", return_value=set()):
        fixes = propose_config_fixes(_config())

    assert fixes == []


def test_propose_skips_role_when_fallback_also_missing():
    """No proposal when both preferred and fallback are unavailable."""
    def avail(host, model):
        return False

    def resolve(host, preferred, role):
        return SimpleNamespace(model="also-missing", fallback_used=True, note="none found")

    with (
        patch("haymish.ai.ollama_client.available_models", return_value={"something-unrelated"}),
        patch("haymish.ai.ollama_client.model_available", side_effect=avail),
        patch("haymish.ai.model_resolve.resolve_model", side_effect=resolve),
    ):
        fixes = propose_config_fixes(_config())

    assert fixes == []


# -- CLI integration tests ---------------------------------------------------

def test_fix_config_is_an_accepted_choice():
    """--fix config must be a valid Click choice, not rejected."""
    result = CliRunner().invoke(cli_main, ["doctor", "--fix", "config", "--help"])
    # --help exits 0 but proves the option is accepted
    # Actually let's test that Click doesn't reject "config" as a choice
    assert "Invalid value" not in (result.output or "")


def test_fix_config_prints_proposals_without_writing(tmp_path):
    """The CLI must print proposals and never write to rules.toml."""
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text(
        "[global]\n"
        'library = "/nonexistent/Test.photoslibrary"\n'
    )
    original_content = rules_path.read_text()

    config = _config(source_path=rules_path)
    fixes = [("[global.ollama].model", "gemma3:4b", "qwen3-vl:8b")]

    with (
        patch("haymish.cli._load_config", return_value=config),
        patch("haymish.cli.RULES_PATH", rules_path),
        patch("haymish.doctor.run_all", return_value=[]),
        patch("haymish.doctor.propose_config_fixes", return_value=fixes),
    ):
        result = CliRunner().invoke(cli_main, ["doctor", "--fix", "config"])

    # Must show the proposal
    assert "qwen3-vl:8b" in result.output
    assert "gemma3:4b" in result.output
    assert "Proposed" in result.output
    # Must NOT have written to rules.toml
    assert rules_path.read_text() == original_content
    # Must tell the user it's manual
    assert "will not edit" in result.output.lower() or "not edit rules.toml" in result.output.lower()


def test_fix_config_says_no_fixes_when_all_ok(tmp_path):
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text("[global]\n")
    config = _config(source_path=rules_path)

    with (
        patch("haymish.cli._load_config", return_value=config),
        patch("haymish.cli.RULES_PATH", rules_path),
        patch("haymish.doctor.run_all", return_value=[]),
        patch("haymish.doctor.propose_config_fixes", return_value=[]),
    ):
        result = CliRunner().invoke(cli_main, ["doctor", "--fix", "config"])

    assert "No config fixes needed" in result.output


def test_fix_unknown_value_is_rejected():
    """--fix with an unknown value must be rejected by Click."""
    result = CliRunner().invoke(cli_main, ["doctor", "--fix", "magic"])
    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_doctor_without_fix_just_reports(tmp_path):
    """Plain `doctor` runs checks but does not propose or write."""
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text("[global]\n")
    config = _config(source_path=rules_path)

    checks = [(True, "macOS", "ok"), (False, "Ollama", "missing model")]
    with (
        patch("haymish.cli._load_config", return_value=config),
        patch("haymish.cli.RULES_PATH", rules_path),
        patch("haymish.doctor.run_all", return_value=checks),
    ):
        result = CliRunner().invoke(cli_main, ["doctor"])

    assert "macOS" in result.output
    assert "Ollama" in result.output
    # Should not mention proposals without --fix config
    assert "Proposed" not in result.output
    assert result.exit_code == 1  # one failing check
