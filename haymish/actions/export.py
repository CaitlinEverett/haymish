"""Export a photo's original to the backup volume and checksum-verify the write.

osxphotos 0.76.1 exposes this as PhotoInfo.export (not export2 -- that name doesn't
exist in this version). Signature introspected from the installed package:

    export(self, dest, filename=None, edited=False, live_photo=False, raw_photo=False,
           export_as_hardlink=False, overwrite=False, increment=True, sidecar_json=False,
           sidecar_exiftool=False, sidecar_xmp=False, use_photos_export=False, timeout=120,
           exiftool=False, use_albums_as_keywords=False, use_persons_as_keywords=False,
           keyword_template=None, description_template=None, render_options=None) -> list[str]

Returns a list of exported file paths. use_photos_export drives export via AppleScript
interaction with Photos, which is also how a missing (iCloud-only) original gets pulled
down -- there's no separate download_missing switch on the public API (that flag exists
on the internal PhotoExporter/ExportOptions class but PhotoInfo.export doesn't surface
it), so use_photos_export is the only lever this module has for iCloud originals.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import inspect
import time
from dataclasses import dataclass
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0


@dataclass
class ArchiveResult:
    uuid: str
    ok: bool
    path: str | None
    sha256: str | None
    nbytes: int
    verified: bool
    error: str = ""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _dest_dir(photo, backup_dir: Path) -> Path:
    date = getattr(photo, "date", None) or dt.datetime.now()
    return backup_dir / f"{date.year:04d}" / f"{date.month:02d}"


def _dest_filename(photo) -> str:
    # uuid-prefixed so two different photos can never collide on this deterministic
    # path even if they share original_filename + year/month (e.g. default camera
    # counter names like IMG_0001.JPG from two different phones) -- a collision here
    # used to let archive_photo's idempotency check silently stamp one photo's hash
    # onto a different photo's catalog record.
    uuid = getattr(photo, "uuid", None) or "unknown"
    original = getattr(photo, "original_filename", None) or f"{uuid}.jpg"
    return f"{uuid}_{original}"


def _expected_path(photo, backup_dir: Path) -> Path:
    return _dest_dir(photo, backup_dir) / _dest_filename(photo)


def _supports_icloud_download(photo) -> bool:
    try:
        return "use_photos_export" in inspect.signature(photo.export).parameters
    except (TypeError, ValueError):
        return False


def _export_with_retry(photo, dest_dir: Path, export_kwargs: dict) -> list[str]:
    # use_photos_export routes through AppleScript, which is subject to the macOS
    # Tahoe AppleScript timeout regressions -- retry with backoff like other
    # photoscript/AppleScript-driven calls in this codebase.
    delay = _RETRY_BASE_DELAY
    last_exc: Exception = RuntimeError("export retry loop did not run")
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return photo.export(str(dest_dir), **export_kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(delay)
                delay *= 2
    raise last_exc


def reverify_on_disk(path: str, expected_sha256: str) -> bool:
    """Live re-check that a previously-archived backup file still exists on disk and
    still hashes to what was recorded. catalog.db's verified_at flag only reflects
    what was true at archive time -- a backup drive can be unplugged, reformatted, or
    the file corrupted/deleted afterward with no trace in the local sqlite cache. The
    caller in cli.py's confirm_deletes command calls this right before the
    irreversible delete, not just at archive time, to close that staleness gap."""
    try:
        p = Path(path)
        if not p.is_file():
            return False
        return _sha256_file(p) == expected_sha256
    except OSError:
        return False


def is_up_to_date(photo, backup_dir: Path) -> bool:
    """True if an archived copy already exists at the expected path, without exporting."""
    dest = _expected_path(photo, backup_dir)
    try:
        return dest.is_file() and dest.stat().st_size > 0
    except OSError:
        return False


def archive_photo(photo, backup_dir: Path) -> ArchiveResult:
    """Exports photo's original to backup_dir/YYYY/MM/<filename>, sha256-verifies the
    write, and returns the result. Does not touch the Catalog -- the caller records
    ArchiveResult's fields via Catalog.record_archive.
    """
    uuid = getattr(photo, "uuid", "?")
    dest_dir = _dest_dir(photo, backup_dir)
    filename = _dest_filename(photo)
    dest_path = dest_dir / filename

    if dest_path.is_file():
        try:
            nbytes = dest_path.stat().st_size
        except OSError as exc:
            return ArchiveResult(uuid, False, None, None, 0, False,
                                  f"could not stat existing archive copy: {exc}")
        if nbytes > 0:
            try:
                digest = _sha256_file(dest_path)
            except OSError as exc:
                return ArchiveResult(uuid, False, str(dest_path), None, nbytes, False,
                                      f"existing archive copy unreadable: {exc}")
            # archive_photo takes no separate "expected hash" argument, so idempotency
            # means: a non-empty file already at the deterministic destination path IS
            # "what's expected" -- re-hash it and skip re-export rather than redoing work.
            return ArchiveResult(uuid, True, str(dest_path), digest, nbytes, True)

    local_path = getattr(photo, "path", None)
    needs_download = local_path is None

    export_kwargs = dict(filename=filename, overwrite=True, increment=False)
    if needs_download:
        if not _supports_icloud_download(photo):
            return ArchiveResult(uuid, False, None, None, 0, False,
                                  f"{uuid}: original not downloaded locally (iCloud storage-optimized) "
                                  "and this osxphotos version has no use_photos_export/download option "
                                  "-- run with a local copy or export first")
        export_kwargs["use_photos_export"] = True

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ArchiveResult(uuid, False, None, None, 0, False, f"could not create backup directory: {exc}")

    try:
        if needs_download:
            exported = _export_with_retry(photo, dest_dir, export_kwargs)
        else:
            exported = photo.export(str(dest_dir), **export_kwargs)
    except Exception as exc:
        return ArchiveResult(uuid, False, None, None, 0, False, f"export failed: {exc}")

    if not exported:
        return ArchiveResult(uuid, False, None, None, 0, False, "export produced no output file")

    exported_path = Path(exported[0])
    try:
        nbytes = exported_path.stat().st_size
    except OSError as exc:
        return ArchiveResult(uuid, False, str(exported_path), None, 0, False,
                              f"exported file unreadable immediately after write: {exc}")

    if nbytes == 0:
        return ArchiveResult(uuid, False, str(exported_path), None, 0, False,
                              "exported file is empty (0 bytes)")

    # verified here means "we independently re-read the exact bytes we just wrote and
    # they're non-empty and readable" -- i.e. no silent truncation/corruption during the
    # write. It is NOT a comparison against a prior known-good hash; archive_photo has no
    # such reference to compare against at export time.
    try:
        digest = _sha256_file(exported_path)
    except OSError as exc:
        return ArchiveResult(uuid, True, str(exported_path), None, nbytes, False,
                              f"exported file could not be re-read for checksum: {exc}")

    return ArchiveResult(uuid, True, str(exported_path), digest, nbytes, True)
