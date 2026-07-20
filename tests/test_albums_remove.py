"""Album remove must normalize osxphotos UUIDs to photoscript Photo.id."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from haymish.actions.albums import remove_from_album


def test_remove_from_album_passes_photoscript_ids_not_bare_uuids():
    """Regression: photoscript compares photo.id ('UUID/L0/001'), so bare UUIDs
    make remove_by_id a silent no-op — undo reports success but leaves the photo."""
    album = MagicMock()
    album.path_str.return_value = "Swept/Screenshots"
    library = MagicMock()
    library.albums.return_value = [album]

    photo = MagicMock()
    photo.id = "AAAA/L0/001"

    with (
        patch("haymish.actions.albums.PhotosLibrary", return_value=library),
        patch("haymish.actions.albums.Photo", return_value=photo),
        patch("haymish.actions.albums._retry", side_effect=lambda fn, *a, **k: fn(*a, **k)),
    ):
        n, failed = remove_from_album(["AAAA"], "Swept/Screenshots")

    assert n == 1
    assert failed == []
    album.remove_by_id.assert_called_once_with(["AAAA/L0/001"])
