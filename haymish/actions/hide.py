"""Hide/unhide via PyObjC PhotoKit (PHAssetChangeRequest.setHidden_), not photoscript.

Reversible: moves assets into/out of Photos' own Hidden album. Unlike photoscript,
PhotoKit calls are native framework calls, not AppleScript, so they aren't subject
to the AppleScript timeout regressions -- no retry-with-backoff needed here.
"""
from __future__ import annotations

import threading

_BATCH_SIZE = 50  # bound the blast radius of a failed transaction to a chunk, not the whole input


def _wait_for_auth() -> int:
    import Photos

    status = Photos.PHPhotoLibrary.authorizationStatusForAccessLevel_(Photos.PHAccessLevelReadWrite)
    if status == Photos.PHAuthorizationStatusAuthorized:
        return status

    done = threading.Event()
    result: dict = {}

    def handler(new_status):
        result["status"] = new_status
        done.set()

    Photos.PHPhotoLibrary.requestAuthorizationForAccessLevel_handler_(
        Photos.PHAccessLevelReadWrite, handler
    )
    done.wait(timeout=120)
    return result.get("status", status)


def _ensure_authorized() -> None:
    import Photos

    status = _wait_for_auth()
    if status != Photos.PHAuthorizationStatusAuthorized:
        raise Exception(
            f"PhotoKit access not authorized (status={status}); grant Full Access to "
            "Photos for this app in System Settings > Privacy & Security > Photos"
        )


def _fetch_assets(uuids: list[str]):
    import Photos

    opts = Photos.PHFetchOptions.alloc().init()
    opts.setIncludeHiddenAssets_(True)  # default fetch excludes already-hidden assets
    fetch = Photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_(uuids, opts)

    by_uuid = {}
    for i in range(fetch.count()):
        asset = fetch.objectAtIndex_(i)
        by_uuid[str(asset.localIdentifier())] = asset
    return by_uuid


def _set_hidden_batch(assets: list, hidden: bool) -> tuple[bool, str]:
    import Photos

    lib = Photos.PHPhotoLibrary.sharedPhotoLibrary()

    def changes():
        for asset in assets:
            req = Photos.PHAssetChangeRequest.changeRequestForAsset_(asset)
            req.setHidden_(hidden)

    ok, error = lib.performChangesAndWait_error_(changes, None)
    return bool(ok), str(error) if error else ""


def _apply(uuids: list[str], hidden: bool) -> dict[str, str]:
    _ensure_authorized()

    results: dict[str, str] = {}
    by_uuid = _fetch_assets(uuids)

    found_uuids = [u for u in uuids if u in by_uuid]
    for u in uuids:
        if u not in by_uuid:
            results[u] = "not found in library"

    for start in range(0, len(found_uuids), _BATCH_SIZE):
        chunk = found_uuids[start : start + _BATCH_SIZE]
        assets = [by_uuid[u] for u in chunk]
        ok, err = _set_hidden_batch(assets, hidden)
        if ok:
            for u in chunk:
                results[u] = "ok"
        else:
            for u in chunk:
                results[u] = err or "performChangesAndWait_error_ failed"

    return results


def hide_photos(uuids: list[str]) -> dict[str, str]:
    """Sets isHidden=True on each asset. Returns uuid -> "ok" or uuid -> error message."""
    return _apply(uuids, True)


def unhide_photos(uuids: list[str]) -> dict[str, str]:
    """Sets isHidden=False on each asset. Returns uuid -> "ok" or uuid -> error message."""
    return _apply(uuids, False)
