"""Unhide photos recorded in the catalog hide ledger (PhotoKit).

Used by `haymish recover-hidden` and scripts/recover_hidden.py. Prefer running
from Terminal.app when Cursor lacks Photos TCC — doctor surfaces the host-specific
System Settings path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .actions.hide import unhide_photos
from .catalog import Catalog
from .doctor import photokit_access_fix_hint


@dataclass
class RecoverReport:
    uuids: list[str] = field(default_factory=list)
    ok: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def hide_uuids_from_ledger(
    catalog: Catalog,
    *,
    run_id: str | None = None,
    uuids: list[str] | None = None,
    limit: int = 10_000,
) -> list[str]:
    """Distinct uuids with logged hide actions, optionally filtered."""
    actions = catalog.recent_actions(run_id=run_id, actions=["hide"], limit=limit)
    if uuids is not None:
        wanted = set(uuids)
        actions = [a for a in actions if a["uuid"] in wanted]
    seen: set[str] = set()
    out: list[str] = []
    for a in actions:
        u = a["uuid"]
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def recover_hidden(
    catalog: Catalog,
    *,
    run_id: str | None = None,
    uuids: list[str] | None = None,
) -> RecoverReport:
    targets = hide_uuids_from_ledger(catalog, run_id=run_id, uuids=uuids)
    report = RecoverReport(uuids=targets)
    if not targets:
        report.errors.append("no hide actions matched — pass UUID(s) or --run-id")
        return report

    try:
        results = unhide_photos(targets)
    except Exception as e:
        report.errors.append(
            f"PhotoKit unhide failed: {e}. Fix: {photokit_access_fix_hint()}. "
            "You can also unhide manually in Photos → Albums → Hidden."
        )
        return report

    for uuid in targets:
        status = results.get(uuid, "unknown")
        if status == "ok":
            report.ok += 1
        else:
            report.failed.append((uuid, str(status)))
    return report
