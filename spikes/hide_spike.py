"""M0 spike: can we hide/unhide a photo from Python via PhotoKit?

Does a full roundtrip on ONE photo — hides it, verifies, unhides it — so it is
safe to run against the real library. PhotoKit always operates on the SYSTEM
photo library (the one designated in Photos → Settings → General).

Usage:
    uv run python spikes/hide_spike.py            # newest screenshot, hide+unhide roundtrip
    uv run python spikes/hide_spike.py <UUID>     # specific asset by UUID
    uv run python spikes/hide_spike.py --leave-hidden   # skip the unhide step

First run triggers the macOS "allow access to Photos" prompt for the host app.
"""

from __future__ import annotations

import sys
import threading

import Photos


def wait_for_auth() -> int:
    status = Photos.PHPhotoLibrary.authorizationStatusForAccessLevel_(Photos.PHAccessLevelReadWrite)
    if status == 3:  # authorized
        return status
    done = threading.Event()
    result = {}

    def handler(new_status):
        result["status"] = new_status
        done.set()

    Photos.PHPhotoLibrary.requestAuthorizationForAccessLevel_handler_(
        Photos.PHAccessLevelReadWrite, handler
    )
    print("Waiting for Photos access approval (check for a macOS dialog)…")
    done.wait(timeout=120)
    return result.get("status", status)


def newest_screenshot():
    options = Photos.PHFetchOptions.alloc().init()
    options.setSortDescriptors_(
        [Photos.NSSortDescriptor.sortDescriptorWithKey_ascending_("creationDate", False)]
    )
    fetch = Photos.PHAsset.fetchAssetsWithMediaType_options_(Photos.PHAssetMediaTypeImage, options)
    for i in range(fetch.count()):
        asset = fetch.objectAtIndex_(i)
        if asset.mediaSubtypes() & Photos.PHAssetMediaSubtypePhotoScreenshot:
            return asset
    return fetch.objectAtIndex_(0) if fetch.count() else None


def asset_by_uuid(uuid: str):
    fetch = Photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_([uuid], None)
    return fetch.objectAtIndex_(0) if fetch.count() else None


def set_hidden(asset, hidden: bool) -> tuple[bool, str]:
    lib = Photos.PHPhotoLibrary.sharedPhotoLibrary()

    def changes():
        req = Photos.PHAssetChangeRequest.changeRequestForAsset_(asset)
        req.setHidden_(hidden)

    ok, error = lib.performChangesAndWait_error_(changes, None)
    return bool(ok), str(error) if error else ""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    leave_hidden = "--leave-hidden" in sys.argv

    status = wait_for_auth()
    if status != 3:
        print(f"FAIL: not authorized (status={status}). Grant Photos access and retry.")
        sys.exit(1)

    asset = asset_by_uuid(args[0]) if args else newest_screenshot()
    if asset is None:
        print("FAIL: no asset found.")
        sys.exit(1)

    print(f"Target: {asset.localIdentifier()} created {asset.creationDate()} hidden={bool(asset.isHidden())}")

    ok, err = set_hidden(asset, True)
    print(f"hide  → ok={ok} {err}")
    if not ok:
        sys.exit(1)

    refreshed = asset_by_uuid(asset.localIdentifier())
    hidden_state = "not-fetchable-by-default-(expected: hidden assets are excluded)" if refreshed is None \
        else f"isHidden={bool(refreshed.isHidden())}"
    print(f"verify → {hidden_state}")

    if not leave_hidden:
        # hidden assets need includeHiddenAssets to refetch
        opts = Photos.PHFetchOptions.alloc().init()
        opts.setIncludeHiddenAssets_(True)
        fetch = Photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_([asset.localIdentifier()], opts)
        if fetch.count():
            ok, err = set_hidden(fetch.objectAtIndex_(0), False)
            print(f"unhide → ok={ok} {err}")
        else:
            print("unhide → FAIL: could not refetch hidden asset")
            sys.exit(1)

    print("SPIKE PASS" if ok else "SPIKE FAIL")


if __name__ == "__main__":
    main()
