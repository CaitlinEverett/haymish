"""Tag photos with keywords via photoscript (AppleScript bridge to Photos.app)."""

from __future__ import annotations

import time

from photoscript import Photo

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0


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


def _read_keywords(photo: Photo) -> list[str]:
    return photo.keywords


def _write_keywords(photo: Photo, keywords: list[str]) -> None:
    photo.keywords = keywords


def set_keyword(uuids: list[str], keyword: str) -> tuple[int, list[str]]:
    """Adds the given keyword to each photo's EXISTING keyword list (must not clobber other
    keywords already on the photo -- read current keywords, append if missing, write back).
    Batched with retry. Returns (n_updated, failed_uuids)."""
    if not uuids:
        return 0, []

    n_updated = 0
    failed: list[str] = []

    for uuid in uuids:
        try:
            photo = _retry(Photo, uuid)
            current = _retry(_read_keywords, photo)
            if keyword not in current:
                _retry(_write_keywords, photo, current + [keyword])
            n_updated += 1
        except Exception:
            failed.append(uuid)

    return n_updated, failed


def remove_keyword(uuids: list[str], keyword: str) -> tuple[int, list[str]]:
    """Undo counterpart to set_keyword. Removes only `keyword`, leaving any other
    keywords on the photo untouched. No-ops on a photo that no longer has it.
    Returns (n_updated, failed_uuids)."""
    if not uuids:
        return 0, []

    n_updated = 0
    failed: list[str] = []

    for uuid in uuids:
        try:
            photo = _retry(Photo, uuid)
            current = _retry(_read_keywords, photo)
            if keyword in current:
                _retry(_write_keywords, photo, [k for k in current if k != keyword])
            n_updated += 1
        except Exception:
            failed.append(uuid)

    return n_updated, failed
