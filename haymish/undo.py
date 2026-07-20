"""Reverses the album/keyword/hide/stage_delete actions of a sweep run.

Archive actions are deliberately NOT reversed -- a backup copy is a safety net,
not something undo should delete. Actual (confirmed) deletions are never
reversible through this tool at all; only Photos' own Recently Deleted (30-day
window) can recover those, and that's outside this codebase's scope by design
(see actions/delete.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .actions import albums, delete as delete_action, hide as hide_action, keywords
from .catalog import Catalog

UNDOABLE_ACTIONS = ("album", "keyword", "hide", "stage_delete")
_UNDO_ACTION_LIMIT = 100_000  # a sweep run's action count, not a display limit


@dataclass
class UndoReport:
    run_id: str
    reversed_counts: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    skipped_not_undoable: int = 0
    skipped_already_resolved: int = 0


def _dedupe_by_uuid(pairs: list[tuple[int, str]]) -> dict[str, list[int]]:
    """Groups (action_id, uuid) pairs by uuid -- two rules can independently hide/file
    the same photo in one run, producing multiple ledger rows for one real action.
    Callers make ONE API call per unique uuid but mark every corresponding action id
    undone once that call succeeds, since all of those rows really did get reversed."""
    by_uuid: dict[str, list[int]] = {}
    for action_id, uuid in pairs:
        by_uuid.setdefault(uuid, []).append(action_id)
    return by_uuid


def undo_run(catalog: Catalog, run_id: str | None = None) -> UndoReport:
    # Default to the most recent apply run (sweep --apply OR review Apply) —
    # not just any run. A scan / dry-run / confirm-deletes after an apply would
    # otherwise become the undo target, report "nothing reversible", and leave
    # the real mutations untouched.
    run_id = run_id or catalog.last_undoable_run_id()
    if run_id is None:
        return UndoReport(run_id="", errors=["no runs recorded yet"])

    actions = catalog.recent_actions(
        run_id=run_id, actions=[*UNDOABLE_ACTIONS, "archive"], limit=_UNDO_ACTION_LIMIT
    )
    report = UndoReport(run_id=run_id)

    album_groups: dict[str, list[tuple[int, str]]] = {}
    keyword_groups: dict[str, list[tuple[int, str]]] = {}
    hide_pairs: list[tuple[int, str]] = []
    stage_delete_pairs: list[tuple[int, str]] = []

    for a in actions:
        pair = (a["id"], a["uuid"])
        if a["action"] == "album":
            album_groups.setdefault(a["detail"]["album"], []).append(pair)
        elif a["action"] == "keyword":
            keyword_groups.setdefault(a["detail"]["keyword"], []).append(pair)
        elif a["action"] == "hide":
            hide_pairs.append(pair)
        elif a["action"] == "stage_delete":
            stage_delete_pairs.append(pair)
        elif a["action"] == "archive":
            report.skipped_not_undoable += 1

    for album, pairs in album_groups.items():
        by_uuid = _dedupe_by_uuid(pairs)
        n, failed = albums.remove_from_album(list(by_uuid), album)
        failed_set = set(failed)
        for uuid, action_ids in by_uuid.items():
            if uuid not in failed_set:
                for action_id in action_ids:
                    catalog.mark_undone(action_id)
        report.reversed_counts["album"] = report.reversed_counts.get("album", 0) + n
        report.errors += [f"could not remove {u} from album {album!r}" for u in failed]

    for keyword, pairs in keyword_groups.items():
        by_uuid = _dedupe_by_uuid(pairs)
        n, failed = keywords.remove_keyword(list(by_uuid), keyword)
        failed_set = set(failed)
        for uuid, action_ids in by_uuid.items():
            if uuid not in failed_set:
                for action_id in action_ids:
                    catalog.mark_undone(action_id)
        report.reversed_counts["keyword"] = report.reversed_counts.get("keyword", 0) + n
        report.errors += [f"could not remove keyword {keyword!r} from {u}" for u in failed]

    if hide_pairs:
        by_uuid = _dedupe_by_uuid(hide_pairs)
        results = hide_action.unhide_photos(list(by_uuid))
        n = 0
        for uuid, action_ids in by_uuid.items():
            if results.get(uuid) == "ok":
                for action_id in action_ids:
                    catalog.mark_undone(action_id)
                n += 1
            else:
                report.errors.append(f"could not unhide {uuid}: {results.get(uuid)}")
        report.reversed_counts["hide"] = n

    if stage_delete_pairs:
        # A staged delete can have already been resolved by `confirm-deletes` --
        # either actually deleted (unstage_all removed it from staged_deletes and a
        # separate, later run logged a "deleted" action) or already unstaged by a
        # prior undo. Only claim "reversed" for uuids that are STILL staged right
        # now; otherwise this would report a permanently-deleted photo as
        # successfully "un-staged," which is true of the bookkeeping but materially
        # misleading about the photo itself.
        currently_staged = {row["uuid"] for row in delete_action.list_staged(catalog)}
        by_uuid = _dedupe_by_uuid(stage_delete_pairs)
        still_staged = {u: ids for u, ids in by_uuid.items() if u in currently_staged}
        already_resolved = {u: ids for u, ids in by_uuid.items() if u not in currently_staged}

        if still_staged:
            delete_action.unstage_all(catalog, list(still_staged))
            for action_ids in still_staged.values():
                for action_id in action_ids:
                    catalog.mark_undone(action_id)
        report.reversed_counts["stage_delete"] = len(still_staged)

        for action_ids in already_resolved.values():
            report.skipped_already_resolved += len(action_ids)

    return report
