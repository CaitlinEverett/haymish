"""Regression: PhotoKit localIdentifier suffix must not break hide lookup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from haymish.actions.hide import _fetch_assets


def test_fetch_assets_indexes_by_bare_uuid():
    asset = MagicMock()
    asset.localIdentifier.return_value = "AAAA-BBBB/L0/001"

    fetch = MagicMock()
    fetch.count.return_value = 1
    fetch.objectAtIndex_.return_value = asset

    photos = MagicMock()
    photos.PHFetchOptions.alloc.return_value.init.return_value = MagicMock()
    photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_.return_value = fetch

    with patch.dict("sys.modules", {"Photos": photos}):
        # Re-import path: _fetch_assets imports Photos inside the function.
        by_uuid = _fetch_assets(["AAAA-BBBB"])

    assert "AAAA-BBBB" in by_uuid
    assert by_uuid["AAAA-BBBB"] is asset
    assert "AAAA-BBBB/L0/001" not in by_uuid
