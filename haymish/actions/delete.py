"""Stage candidates for deletion and perform the actual PhotoKit delete.

Division of responsibility (read this before calling confirm_and_delete):
  - stage_for_delete() is pure bookkeeping. A sweep rule's "delete" stage only ever
    stages -- it never calls confirm_and_delete directly.
  - confirm_and_delete() is the thin last-mile PhotoKit call. It does NOT check
    backups and does NOT read staged_deletes -- it just deletes whatever uuids it's
    given, gated only by macOS's own un-bypassable system confirmation dialog.
  - The complete asset-component manifest gate and typed confirmation belong to
    the caller -- the "haymish confirm-deletes" CLI command -- not to this adapter.
    That command currently fails closed because legacy Catalog archive rows are not
    complete manifests. Keeping this file free of policy makes the one function that
    invokes PhotoKit deletion easy to audit in isolation.
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


def _bare_uuid(local_identifier: str) -> str:
    """PhotoKit localIdentifiers look like '{uuid}/L0/001'; haymish ledgers store the bare uuid."""
    return str(local_identifier).split("/", 1)[0]


def _distinct_bare(identifiers) -> list[str]:
    """Bare uuids in first-seen order; '{uuid}' and '{uuid}/L0/001' are one asset."""
    out: list[str] = []
    seen: set[str] = set()
    for ident in identifiers:
        bare = _bare_uuid(ident)
        if bare in seen:
            continue
        seen.add(bare)
        out.append(bare)
    return out


def _fetch_keys(uuids: list[str]) -> list[str]:
    """Fetch keys for PHAsset.fetchAssetsWithLocalIdentifiers_options_.

    A caller-supplied full identifier is passed through verbatim -- the /L0/NNN
    suffix selects a specific asset resource and is not ours to rewrite (guessing
    /L0/001 for something the caller called /L0/002 would fetch the wrong asset or
    nothing at all). A bare catalog uuid has no suffix to preserve, so we also try
    the /L0/001 form that every identifier observed from this library uses; some
    PhotoKit versions match only one of the two.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for u in uuids:
        ident = str(u)
        candidates = [ident] if "/" in ident else [ident, f"{ident}/L0/001"]
        for key in candidates:
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


def _fetch_assets_by_uuid(uuids: list[str]):
    import Photos

    options = Photos.PHFetchOptions.alloc().init()
    options.setIncludeHiddenAssets_(True)
    return Photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_(_fetch_keys(uuids), options)


def confirm_and_delete(uuids: list[str]) -> DeleteOutcome:
    """Deletes uuids via PHAssetChangeRequest.deleteAssets_ inside a PhotoKit change
    block. macOS shows its own confirmation dialog listing the assets and this call
    BLOCKS until the user responds there -- that dialog is the only confirmation
    this function performs or requires.

    Deleted assets land in Photos' Recently Deleted (30-day recovery window) as a
    built-in PhotoKit behavior; nothing extra is done here to support that.
    """
    import Photos

    # Counted as distinct assets, not raw arguments: cli.py reports
    # `requested - len(deleted_uuids)` as "still staged", so counting '{uuid}' and
    # '{uuid}/L0/001' twice would invent a phantom missing photo.
    requested = len(_distinct_bare(uuids))
    if not uuids:
        return DeleteOutcome(requested=0, deleted_uuids=[])

    fetch = _fetch_assets_by_uuid(uuids)
    # localIdentifier() always returns the suffixed form. The caller (confirm-deletes)
    # feeds these straight back into catalog.unstage_delete(), which keys on the bare
    # uuid -- without normalizing, every successful delete leaves its row staged.
    # Deduped because both fetch keys for one asset can match and return it twice.
    found_uuids = _distinct_bare(
        fetch.objectAtIndex_(i).localIdentifier() for i in range(fetch.count())
    )
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
