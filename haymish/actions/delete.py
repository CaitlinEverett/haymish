"""Stage candidates for deletion and perform the actual PhotoKit delete.

Division of responsibility (read this before calling confirm_and_delete):
  - stage_for_delete() is pure bookkeeping. A sweep rule's "delete" stage only ever
    stages -- it never calls confirm_and_delete directly.
  - confirm_and_delete() is the thin last-mile PhotoKit call. It does NOT check
    backups and does NOT read staged_deletes -- it just deletes whatever uuids it's
    given, gated only by macOS's own un-bypassable system confirmation dialog.
  - The backup-verification gate (every uuid must satisfy
    Catalog.is_archived_and_verified()) and any additional typed-confirmation
    prompt belong to the caller -- the "haymish confirm-deletes" CLI command --
    not to this module. Keeping this file free of that logic keeps the one function
    that actually destroys data easy to audit in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def stage_for_delete(catalog, run_id: str, rule: str, uuids: list[str]) -> int:
    count = 0
    for uuid in uuids:
        catalog.stage_delete(uuid, rule, run_id)
        count += 1
    return count


def list_staged(catalog) -> list[dict]:
    return catalog.list_staged_deletes()


@dataclass
class DeleteOutcome:
    requested: int
    deleted_uuids: list[str] = field(default_factory=list)
    cancelled: bool = False
    error: str = ""


def _fetch_assets_by_uuid(uuids: list[str]):
    import Photos

    options = Photos.PHFetchOptions.alloc().init()
    options.setIncludeHiddenAssets_(True)
    return Photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_(uuids, options)


def confirm_and_delete(uuids: list[str]) -> DeleteOutcome:
    """Deletes uuids via PHAssetChangeRequest.deleteAssets_ inside a PhotoKit change
    block. macOS shows its own confirmation dialog listing the assets and this call
    BLOCKS until the user responds there -- that dialog is the only confirmation
    this function performs or requires.

    Deleted assets land in Photos' Recently Deleted (30-day recovery window) as a
    built-in PhotoKit behavior; nothing extra is done here to support that.
    """
    import Photos

    requested = len(uuids)
    if not uuids:
        return DeleteOutcome(requested=0, deleted_uuids=[])

    fetch = _fetch_assets_by_uuid(uuids)
    found_uuids = [fetch.objectAtIndex_(i).localIdentifier() for i in range(fetch.count())]
    if not found_uuids:
        return DeleteOutcome(requested=requested, deleted_uuids=[],
                              error="none of the requested uuids were found in the library")

    library = Photos.PHPhotoLibrary.sharedPhotoLibrary()

    def changes():
        Photos.PHAssetChangeRequest.deleteAssets_(fetch)

    try:
        ok, error = library.performChangesAndWait_error_(changes, None)
    except Exception as exc:
        return DeleteOutcome(requested=requested, deleted_uuids=[], error=str(exc))

    if not ok:
        detail = str(error) if error else ""
        if "cancel" in detail.lower():
            return DeleteOutcome(requested=requested, deleted_uuids=[], cancelled=True)
        return DeleteOutcome(requested=requested, deleted_uuids=[], error=detail or "delete failed")

    return DeleteOutcome(requested=requested, deleted_uuids=found_uuids)


def unstage_all(catalog, uuids: list[str]):
    for uuid in uuids:
        catalog.unstage_delete(uuid)
