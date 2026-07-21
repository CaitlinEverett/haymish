"""Hide/unhide via PyObjC PhotoKit (PHAssetChangeRequest.setHidden_), not photoscript.

Reversible: moves assets into/out of Photos' own Hidden album. Unlike photoscript,
PhotoKit calls are native framework calls, not AppleScript, so they aren't subject
to the AppleScript timeout regressions -- no retry-with-backoff needed here.

iCloud note: for assets that aren't downloaded locally (osxphotos ismissing=True),
`fetchAssetsWithLocalIdentifiers` often stops returning the asset once it is hidden
— even with includeHiddenAssets. Unhide therefore also searches the Hidden smart
album. Prefer hiding locally-available photos when dogfooding.
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


def _bare_uuid(local_identifier: str) -> str:
    """PhotoKit localIdentifiers look like '{uuid}/L0/001'; haymish ledgers store the bare uuid."""
    return local_identifier.split("/", 1)[0]


def _fetch_options():
    import Photos

    opts = Photos.PHFetchOptions.alloc().init()
    opts.setIncludeHiddenAssets_(True)  # default fetch excludes already-hidden assets
    return opts


def _fetch_assets(uuids: list[str]):
    import Photos

    opts = _fetch_options()
    # Pass both bare and suffixed forms — some PhotoKit versions are picky after hide.
    keys: list[str] = []
    for u in uuids:
        keys.append(u)
        if "/" not in u:
            keys.append(f"{u}/L0/001")
    fetch = Photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_(keys, opts)

    # Index by bare UUID — fetch accepts bare or suffixed ids, but localIdentifier()
    # always returns the suffixed form. Without this normalization every hide/unhide
    # looks up the caller's bare UUID, misses, and reports "not found in library"
    # even though PhotoKit auth and the fetch both succeeded.
    by_uuid = {}
    for i in range(fetch.count()):
        asset = fetch.objectAtIndex_(i)
        by_uuid[_bare_uuid(str(asset.localIdentifier()))] = asset
    return by_uuid


def _fetch_from_hidden_album(uuids: list[str]) -> dict:
    """Fallback when localIdentifier fetch misses already-hidden iCloud assets."""
    import Photos

    wanted = set(uuids)
    opts = _fetch_options()
    collections = Photos.PHAssetCollection.fetchAssetCollectionsWithType_subtype_options_(
        Photos.PHAssetCollectionTypeSmartAlbum,
        Photos.PHAssetCollectionSubtypeSmartAlbumAllHidden,
        None,
    )
    found = {}
    if collections.count() == 0:
        return found
    assets = Photos.PHAsset.fetchAssetsInAssetCollection_options_(
        collections.objectAtIndex_(0), opts
    )
    for i in range(assets.count()):
        asset = assets.objectAtIndex_(i)
        bare = _bare_uuid(str(asset.localIdentifier()))
        if bare in wanted:
            found[bare] = asset
            if len(found) == len(wanted):
                break
    return found


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
    # Unhide path: iCloud-only assets often vanish from localIdentifier fetch once
    # hidden; the Hidden smart album is the reliable second look.
    if not hidden:
        missing = [u for u in uuids if u not in by_uuid]
        if missing:
            by_uuid.update(_fetch_from_hidden_album(missing))

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
