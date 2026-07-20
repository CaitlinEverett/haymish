"""File photos into albums via photoscript (AppleScript bridge to Photos.app)."""

from __future__ import annotations

import time

from photoscript import Album, Photo, PhotosLibrary

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0
_BATCH_SIZE = 50


def _retry(fn, *args, **kwargs):
    delay = _RETRY_BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # AppleScript bridge is flaky; macOS Tahoe timeout regressions
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(delay)
                delay *= 2
    raise last_exc


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def ensure_album(album_path: str) -> Album:
    """album_path like 'Swept/Ads & Products' (slash = folder nesting). Creates the folder
    chain and album if missing. Returns the photoscript Album object. Idempotent -- safe to
    call every sweep run."""
    parts = [p for p in album_path.split("/") if p]
    if not parts:
        raise ValueError(f"invalid album_path: {album_path!r}")

    library = _retry(PhotosLibrary)

    if len(parts) == 1:
        album = _retry(library.album, parts[0], top_level=True)
        if album is None:
            album = _retry(library.create_album, parts[0])
        return album

    album_name, folder_path = parts[-1], parts[:-1]
    return _retry(library.make_album_folders, album_name, folder_path)


def add_to_album(uuids: list[str], album_path: str) -> tuple[int, list[str]]:
    """Adds photos (osxphotos UUIDs) to album_path, creating it via ensure_album if needed.
    Skips photos already in the album (don't error on duplicates). Batched with retry per
    the conventions above. Returns (n_added, failed_uuids)."""
    if not uuids:
        return 0, []

    try:
        album = ensure_album(album_path)
    except Exception:
        return 0, list(uuids)

    photos: list[Photo] = []
    failed: list[str] = []
    for uuid in uuids:
        try:
            photos.append(_retry(Photo, uuid))
        except Exception:
            failed.append(uuid)

    n_added = 0
    for batch in _chunks(photos, _BATCH_SIZE):
        try:
            added = _retry(album.add, batch)
            n_added += len(added)
        except Exception:
            failed.extend(p.uuid for p in batch)

    return n_added, failed


def remove_from_album(uuids: list[str], album_path: str) -> tuple[int, list[str]]:
    """Undo counterpart to add_to_album. No-ops (doesn't fail) on an already-missing
    album -- undo must be safe to run even if the library changed since the original
    action. Returns (n_removed, failed_uuids).

    photoscript's Album has no direct remove(); Album.remove_by_id() is the real API
    and it rebuilds the whole album (creates a new album with the same name minus the
    removed photos, deletes the old one) rather than removing in place -- Photos'
    AppleScript dictionary has no native "remove from album" verb. Because of that
    rebuild cost, this calls remove_by_id ONCE with the full uuid list rather than
    chunking like add_to_album does.

    Critical: remove_by_id compares against photoscript's Photo.id, which is
    "{uuid}/L0/001" (media-item suffix), NOT the bare osxphotos UUID our ledger
    stores. Passing bare UUIDs makes the filter a no-op — the album is rebuilt
    with every photo still in it, and undo silently "succeeds." Normalize to
    Photo.id before calling.
    """
    if not uuids:
        return 0, []

    # Match on the full folder path (Album.path_str), not bare leaf name -- a
    # leaf-name-only match can hit a same-named album in a different folder (e.g.
    # "Ads & Products" nested somewhere other than "Swept/"), silently rebuilding
    # the wrong album while reporting success.
    library = _retry(PhotosLibrary)
    album = next(
        (a for a in _retry(library.albums) if _retry(a.path_str, "/") == album_path), None
    )
    if album is None:
        return 0, []  # album already gone (or path no longer matches) -- nothing to undo

    photo_ids: list[str] = []
    failed: list[str] = []
    for uuid in uuids:
        try:
            photo_ids.append(_retry(Photo, uuid).id)
        except Exception:
            failed.append(uuid)

    if not photo_ids:
        return 0, failed

    try:
        _retry(album.remove_by_id, photo_ids)
        return len(photo_ids), failed
    except Exception:
        return 0, list(uuids)
