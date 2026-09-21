"""Regression: PhotoKit localIdentifier suffix must not break delete unstaging.

Fully isolated -- Photos is a MagicMock injected into sys.modules, so nothing here
can reach the real photo library.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from haymish.actions import delete as delete_action
from haymish.catalog import Catalog
from haymish.cli import main as cli_main


def _fake_photos(local_identifiers: list[str], ok: bool = True, error=None):
    """A stand-in Photos module whose fetch returns one asset per local identifier."""
    assets = []
    for ident in local_identifiers:
        asset = MagicMock()
        asset.localIdentifier.return_value = ident
        assets.append(asset)

    fetch = MagicMock()
    fetch.count.return_value = len(assets)
    fetch.objectAtIndex_.side_effect = lambda i: assets[i]

    photos = MagicMock()
    photos.PHFetchOptions.alloc.return_value.init.return_value = MagicMock()
    photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_.return_value = fetch
    library = photos.PHPhotoLibrary.sharedPhotoLibrary.return_value

    def perform_changes(block, _error_out):
        # Real PhotoKit runs the change block; a fake that skips it would let a
        # confirm_and_delete that never calls deleteAssets_ pass every success test.
        block()
        return (ok, error)

    library.performChangesAndWait_error_.side_effect = perform_changes
    return photos


def test_deleted_uuids_are_bare_even_when_photokit_returns_suffixed():
    photos = _fake_photos(["AAAA-BBBB/L0/001"])

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["AAAA-BBBB"])

    assert outcome.deleted_uuids == ["AAAA-BBBB"]
    assert outcome.requested == 1
    assert not outcome.cancelled
    assert outcome.error == ""


def test_fetch_uses_hidden_inclusive_options_and_both_identifier_forms():
    photos = _fake_photos(["AAAA-BBBB/L0/001"])

    with patch.dict("sys.modules", {"Photos": photos}):
        # A caller may pass either form; both must collapse to one deduped uuid.
        delete_action.confirm_and_delete(["AAAA-BBBB", "AAAA-BBBB/L0/001"])

    options = photos.PHFetchOptions.alloc.return_value.init.return_value
    options.setIncludeHiddenAssets_.assert_called_once_with(True)

    keys, passed_options = photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_.call_args[0]
    assert keys == ["AAAA-BBBB", "AAAA-BBBB/L0/001"]
    assert passed_options is options


def test_a_caller_supplied_suffix_is_never_rewritten_to_l0_001():
    """A/L0/002 names a different asset resource than A/L0/001 -- guessing loses it."""
    photos = _fake_photos(["AAAA-BBBB/L0/002"])

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["AAAA-BBBB/L0/002"])

    keys, _ = photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_.call_args[0]
    assert keys == ["AAAA-BBBB/L0/002"]
    assert outcome.deleted_uuids == ["AAAA-BBBB"]
    assert outcome.requested == 1


def test_duplicate_aliases_for_one_asset_count_as_one_request():
    """Both fetch keys can match, so the same asset comes back twice; that is not
    two photos, and requested must not exceed it or cli.py invents a missing one."""
    photos = _fake_photos(["AAAA-BBBB", "AAAA-BBBB/L0/001"])

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["AAAA-BBBB", "AAAA-BBBB/L0/001"])

    assert outcome.requested == 1
    assert outcome.deleted_uuids == ["AAAA-BBBB"]
    assert len(outcome.deleted_uuids) == outcome.requested


def test_partial_fetch_reports_only_the_found_uuid_as_deleted():
    photos = _fake_photos(["AAAA-BBBB/L0/001"])

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["AAAA-BBBB", "MISSING-1"])

    assert outcome.requested == 2
    assert outcome.deleted_uuids == ["AAAA-BBBB"]
    assert outcome.error == ""


def test_the_change_block_actually_calls_delete_assets_with_the_fetch():
    photos = _fake_photos(["AAAA-BBBB/L0/001"])

    with patch.dict("sys.modules", {"Photos": photos}):
        delete_action.confirm_and_delete(["AAAA-BBBB"])

    fetch = photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_.return_value
    photos.PHAssetChangeRequest.deleteAssets_.assert_called_once_with(fetch)


def test_partial_delete_against_a_real_catalog_leaves_the_missing_row_staged(tmp_path: Path):
    catalog = Catalog(tmp_path / "catalog.db")
    delete_action.stage_for_delete(catalog, "run-1", "screenshots-general",
                                   ["AAAA-BBBB", "MISSING-1"])
    photos = _fake_photos(["AAAA-BBBB/L0/001"])

    staged_uuids = [row["uuid"] for row in delete_action.list_staged(catalog)]
    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(staged_uuids)
    delete_action.unstage_all(catalog, outcome.deleted_uuids)

    assert [row["uuid"] for row in delete_action.list_staged(catalog)] == ["MISSING-1"]
    assert outcome.requested == 2
    assert outcome.deleted_uuids == ["AAAA-BBBB"]
    catalog.close()


def test_no_assets_found_is_an_error_and_nothing_is_destroyed():
    photos = _fake_photos([])

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["MISSING-1", "MISSING-2"])

    assert outcome.requested == 2
    assert outcome.deleted_uuids == []
    assert not outcome.cancelled
    assert outcome.error == "none of the requested uuids were found in the library"
    # Nothing may be destroyed when the fetch came back empty.
    photos.PHAssetChangeRequest.deleteAssets_.assert_not_called()


def test_empty_input_is_a_no_op():
    photos = _fake_photos([])

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete([])

    assert outcome.requested == 0
    assert outcome.deleted_uuids == []
    assert outcome.error == ""
    photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_.assert_not_called()


def test_user_cancellation_in_the_macos_dialog_reports_cancelled():
    photos = _fake_photos(["AAAA-BBBB/L0/001"], ok=False, error="The operation was cancelled.")

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["AAAA-BBBB"])

    assert outcome.cancelled
    assert outcome.deleted_uuids == []
    assert outcome.error == ""


def test_failure_other_than_cancellation_reports_error():
    photos = _fake_photos(["AAAA-BBBB/L0/001"], ok=False, error="disk on fire")

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["AAAA-BBBB"])

    assert not outcome.cancelled
    assert outcome.deleted_uuids == []
    assert outcome.error == "disk on fire"


def test_successful_delete_of_multiple_assets_returns_all_bare_uuids():
    photos = _fake_photos(["AAAA-BBBB/L0/001", "CCCC-DDDD/L0/001"])

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["AAAA-BBBB", "CCCC-DDDD"])

    assert outcome.deleted_uuids == ["AAAA-BBBB", "CCCC-DDDD"]
    assert outcome.requested == 2
    assert outcome.error == ""


def test_unstage_all_receives_bare_uuids_from_a_delete_outcome():
    """The end-to-end contract cli.py relies on: staged rows actually get cleared."""
    photos = _fake_photos(["AAAA-BBBB/L0/001"])

    with patch.dict("sys.modules", {"Photos": photos}):
        outcome = delete_action.confirm_and_delete(["AAAA-BBBB"])

    catalog = MagicMock()
    delete_action.unstage_all(catalog, outcome.deleted_uuids)

    catalog.unstage_delete.assert_called_once_with("AAAA-BBBB")


def test_confirm_deletes_has_no_backup_bypass_option():
    result = CliRunner().invoke(
        cli_main, ["confirm-deletes", "--no-backup-i-understand"]
    )

    assert result.exit_code == 2
    assert "No such option" in result.output


def test_confirm_deletes_fails_closed_until_complete_archive_manifests_exist():
    catalog = MagicMock()
    catalog.list_staged_deletes.return_value = [
        {
            "uuid": "AAAA-BBBB",
            "rule": "screenshots-general",
            "staged_at": "2026-09-02T14:00:00+00:00",
        }
    ]
    config = SimpleNamespace(library=Path("/nonexistent/Test.photoslibrary"))

    with (
        patch("haymish.cli._load_config", return_value=config),
        patch("haymish.catalog.Catalog", return_value=catalog),
        patch("haymish.library.load_photosdb") as load_photosdb,
        patch("haymish.actions.delete.confirm_and_delete") as confirm,
    ):
        result = CliRunner().invoke(cli_main, ["confirm-deletes"])

    assert result.exit_code == 1
    load_photosdb.assert_not_called()
    confirm.assert_not_called()
    catalog.close.assert_called_once_with()


def test_confirm_deletes_fails_closed_before_photokit_when_backup_is_missing():
    catalog = MagicMock()
    catalog.list_staged_deletes.return_value = [
        {
            "uuid": "AAAA-BBBB",
            "rule": "screenshots-general",
            "staged_at": "2026-09-02T14:00:00+00:00",
        }
    ]
    catalog.get_archive.return_value = None
    config = SimpleNamespace(library=Path("/nonexistent/Test.photoslibrary"))

    with (
        patch("haymish.cli._COMPLETE_ARCHIVE_MANIFESTS_SUPPORTED", True),
        patch("haymish.cli._load_config", return_value=config),
        patch("haymish.catalog.Catalog", return_value=catalog),
        patch("haymish.library.load_photosdb", return_value=object()),
        patch("haymish.library.all_photos", return_value=[]),
        patch("haymish.actions.delete.confirm_and_delete") as confirm,
    ):
        result = CliRunner().invoke(cli_main, ["confirm-deletes"])

    assert result.exit_code == 1
    confirm.assert_not_called()
    catalog.close.assert_called_once_with()
