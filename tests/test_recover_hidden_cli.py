"""recover-hidden CLI argument validation and contracts.

The actual PhotoKit unhide is tested elsewhere; these test the CLI's argument
gating, error messaging, and the composition with catalog ledger lookups.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from haymish.cli import main as cli_main
from haymish.recover_hidden import RecoverReport, hide_uuids_from_ledger, recover_hidden


# -- CLI argument tests -------------------------------------------------------

def test_no_args_exits_with_error():
    result = CliRunner().invoke(cli_main, ["recover-hidden"])
    assert result.exit_code != 0
    assert "UUID" in result.output or "--run-id" in result.output


def test_uuids_are_passed_as_positional_args():
    """One or more UUIDs should be accepted as positional arguments."""
    report = RecoverReport(uuids=["AAA"], ok=1)

    with (
        patch("haymish.recover_hidden.unhide_photos", return_value={"AAA": "ok"}),
        patch("haymish.catalog.Catalog") as MockCatalog,
    ):
        catalog = MockCatalog.return_value
        catalog.recent_actions.return_value = [{"uuid": "AAA", "rule": "test", "ts": ""}]
        result = CliRunner().invoke(cli_main, ["recover-hidden", "AAA"])

    # Should have attempted to unhide
    assert result.exit_code == 0


def test_run_id_is_accepted():
    """--run-id should be accepted and filter the ledger."""
    with (
        patch("haymish.recover_hidden.unhide_photos", return_value={"BBB": "ok"}),
        patch("haymish.catalog.Catalog") as MockCatalog,
    ):
        catalog = MockCatalog.return_value
        catalog.recent_actions.return_value = [{"uuid": "BBB", "rule": "test", "ts": ""}]
        result = CliRunner().invoke(cli_main, ["recover-hidden", "--run-id", "run-abc"])

    assert result.exit_code == 0


def test_both_uuids_and_run_id_accepted():
    """UUIDs and --run-id can be combined."""
    with (
        patch("haymish.recover_hidden.unhide_photos", return_value={"CCC": "ok"}),
        patch("haymish.catalog.Catalog") as MockCatalog,
    ):
        catalog = MockCatalog.return_value
        catalog.recent_actions.return_value = [{"uuid": "CCC", "rule": "test", "ts": ""}]
        result = CliRunner().invoke(cli_main, [
            "recover-hidden", "--run-id", "run-abc", "CCC",
        ])

    assert result.exit_code == 0


# -- hide_uuids_from_ledger tests --------------------------------------------

def test_ledger_deduplicates_uuids(tmp_path):
    from haymish.catalog import Catalog

    catalog = Catalog(tmp_path / "catalog.db")
    run_id = catalog.start_run("test-hide")
    # Log the same uuid twice (as would happen if a rule hid it, then a re-run tried again)
    catalog.log_action(run_id, "rule-a", "DDD", "hide", {})
    catalog.log_action(run_id, "rule-a", "DDD", "hide", {})
    catalog.log_action(run_id, "rule-a", "EEE", "hide", {})
    catalog.finish_run(run_id, {})

    uuids = hide_uuids_from_ledger(catalog, run_id=run_id)
    catalog.close()

    assert set(uuids) == {"DDD", "EEE"}
    assert len(uuids) == 2  # deduplicated


def test_ledger_filters_by_uuid(tmp_path):
    from haymish.catalog import Catalog

    catalog = Catalog(tmp_path / "catalog.db")
    run_id = catalog.start_run("test-hide")
    catalog.log_action(run_id, "rule-a", "FFF", "hide", {})
    catalog.log_action(run_id, "rule-a", "GGG", "hide", {})
    catalog.finish_run(run_id, {})

    uuids = hide_uuids_from_ledger(catalog, run_id=run_id, uuids=["FFF"])
    catalog.close()

    assert uuids == ["FFF"]


# -- recover_hidden logic tests -----------------------------------------------

def test_recover_reports_no_matches():
    catalog = MagicMock()
    catalog.recent_actions.return_value = []

    report = recover_hidden(catalog, uuids=["MISSING"])
    assert report.uuids == []
    assert report.errors


def test_recover_reports_partial_failures():
    catalog = MagicMock()
    catalog.recent_actions.return_value = [
        {"uuid": "OK1", "rule": "r", "ts": ""},
        {"uuid": "FAIL1", "rule": "r", "ts": ""},
    ]

    with patch("haymish.recover_hidden.unhide_photos",
               return_value={"OK1": "ok", "FAIL1": "PhotoKit error"}):
        report = recover_hidden(catalog, uuids=["OK1", "FAIL1"])

    assert report.ok == 1
    assert len(report.failed) == 1
    assert report.failed[0] == ("FAIL1", "PhotoKit error")


def test_recover_catches_total_auth_failure():
    catalog = MagicMock()
    catalog.recent_actions.return_value = [{"uuid": "X", "rule": "r", "ts": ""}]

    with patch("haymish.recover_hidden.unhide_photos",
               side_effect=Exception("not authorized")):
        report = recover_hidden(catalog, uuids=["X"])

    assert report.ok == 0
    assert report.errors
    assert "not authorized" in report.errors[0]
