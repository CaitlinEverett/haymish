"""ask --save gate: the rule is written to rules.toml ONLY after a successful Apply.

Cancelled reviews, empty matches, and Ctrl-C must never leave a stale rule behind.
The CLI's `ask` command already implements this; these tests pin the contract.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from haymish.cli import main as cli_main


def _fake_config(tmp_path: Path):
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text(
        "[global]\n"
        'library = "/nonexistent/Test.photoslibrary"\n'
    )
    return SimpleNamespace(
        library=Path("/nonexistent/Test.photoslibrary"),
        backup=None,
        report_dir=tmp_path / "reports",
        ollama_host="http://localhost:11434",
        ollama_model="gemma3:4b",
        claude_model="claude-sonnet-5",
        ai_embed_model="qwen3-embedding:8b",
        ai_vision_model="gemma3:4b",
        ai_planner_backend="ollama",
        ai_planner_model="qwen3.6:27b",
        rules=[],
        source_path=rules_path,
        posture="tidy",
    )


def _fake_plan():
    from haymish.ai.planner import Plan
    from haymish.config import Rule

    rule = Rule(name="recipe-screenshots", query={"screenshot": True},
                file={"album": "Recipes"})
    return Plan(
        rule=rule,
        raw={"name": "recipe-screenshots", "query": {"screenshot": True},
             "file": {"album": "Recipes"}},
        description="File recipe screenshots into the Recipes album.",
    )


def test_save_does_not_write_when_review_returns_none(tmp_path):
    """Cancelled/empty review -> no rule written, message explains why."""
    config = _fake_config(tmp_path)
    plan = _fake_plan()

    with (
        patch("haymish.cli._load_config", return_value=config),
        patch("haymish.catalog.Catalog", return_value=MagicMock()),
        patch("haymish.library.load_photosdb", return_value=MagicMock()),
        patch("haymish.library.all_photos", return_value=[]),
        patch("haymish.ai.planner.plan_from_prompt", return_value=plan),
        patch("haymish.review.run_review", return_value=None),
    ):
        result = CliRunner().invoke(cli_main, [
            "ask", "file recipe screenshots", "--save", "recipes", "--no-open"
        ])

    assert result.exit_code == 0
    content = config.source_path.read_text()
    assert "[rule." not in content
    assert "Did not save" in result.output


def test_save_writes_after_successful_apply(tmp_path):
    """Successful apply -> rule appended to rules.toml."""
    config = _fake_config(tmp_path)
    plan = _fake_plan()

    from haymish.sweep import RuleOutcome, SweepReport

    fake_report = SweepReport(
        run_id="run-1", apply=True,
        generated="2026-09-21T00:00:00",
        outcomes=[RuleOutcome(rule="recipe-screenshots", matched=3, filed=3)],
    )

    with (
        patch("haymish.cli._load_config", return_value=config),
        patch("haymish.catalog.Catalog", return_value=MagicMock()),
        patch("haymish.library.load_photosdb", return_value=MagicMock()),
        patch("haymish.library.all_photos", return_value=[]),
        patch("haymish.ai.planner.plan_from_prompt", return_value=plan),
        patch("haymish.review.run_review", return_value=fake_report),
    ):
        result = CliRunner().invoke(cli_main, [
            "ask", "file recipe screenshots", "--save", "recipes", "--no-open"
        ])

    assert result.exit_code == 0
    content = config.source_path.read_text()
    assert "[rule.recipes]" in content
    assert "Saved" in result.output


def test_save_without_flag_never_writes(tmp_path):
    """No --save flag -> no rule written even after successful apply."""
    config = _fake_config(tmp_path)
    plan = _fake_plan()

    from haymish.sweep import RuleOutcome, SweepReport

    fake_report = SweepReport(
        run_id="run-1", apply=True,
        generated="2026-09-21T00:00:00",
        outcomes=[RuleOutcome(rule="recipe-screenshots", matched=3, filed=3)],
    )

    with (
        patch("haymish.cli._load_config", return_value=config),
        patch("haymish.catalog.Catalog", return_value=MagicMock()),
        patch("haymish.library.load_photosdb", return_value=MagicMock()),
        patch("haymish.library.all_photos", return_value=[]),
        patch("haymish.ai.planner.plan_from_prompt", return_value=plan),
        patch("haymish.review.run_review", return_value=fake_report),
    ):
        result = CliRunner().invoke(cli_main, [
            "ask", "file recipe screenshots", "--no-open"
        ])

    assert result.exit_code == 0
    content = config.source_path.read_text()
    assert "[rule." not in content
