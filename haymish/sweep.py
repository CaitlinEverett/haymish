"""The rules engine: query -> classify -> lifecycle actions -> log, per rule.toml rule.

Dry-run by default (the caller decides apply=True/False). Rules run in file order;
`exclude_matched_by` only sees rules that already ran earlier in that order (see the
ordering note in rules-template.toml) -- this is not re-checked here, it's a config
authoring concern.

Lifecycle stages (file/hide/archive/delete) are independent age-gates evaluated every
run against the photo's own date, not against when it was first matched -- so a photo
that's already old when first matched can satisfy several stages in the same run.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import library
from .actions import albums, delete as delete_action, export as export_action, hide as hide_action, keywords
from .catalog import Catalog, prompt_hash
from .classify.base import ClassifyError, get_backend
from .config import Config, Rule
from .detectors import dupes as det_dupes, junk as det_junk, messages as det_messages, receipts as det_receipts

DETECTOR_MODULES = {
    "receipts": det_receipts,
    "messages": det_messages,
    "dupes": det_dupes,
    "junk": det_junk,
}

_CLASSIFY_MODEL_FOR_HASH = {"apple": "vision-heuristic"}  # apple backend has no configurable model


@dataclass
class RuleOutcome:
    rule: str
    report_only: bool = False
    matched: int = 0
    filed: int = 0
    hidden: int = 0
    archived: int = 0
    staged_deletes: int = 0
    classify_errors: int = 0
    action_errors: list[str] = field(default_factory=list)


@dataclass
class SweepReport:
    run_id: str
    apply: bool
    generated: str
    outcomes: list[RuleOutcome]


@dataclass
class PreviewCandidate:
    uuid: str
    filename: str
    date: str
    classify_detail: str = ""


@dataclass
class RulePreview:
    rule: Rule
    candidates: list  # actual Photo objects -- kept around so apply_confirmed can act on them directly
    preview_candidates: list[PreviewCandidate]


def _classify_model(config: Config, backend: str) -> str:
    if backend == "ollama":
        return config.ollama_model
    if backend == "claude":
        return config.claude_model
    return _CLASSIFY_MODEL_FOR_HASH.get(backend, backend)


def _select_candidates(rule: Rule, photos: list, by_uuid: dict, now) -> list:
    base = [p for p in photos if library.matches_query(p, rule.query, now)]
    if rule.detector:
        result = DETECTOR_MODULES[rule.detector].detect(base)
        return [by_uuid[c.uuid] for c in result.candidates if c.uuid in by_uuid]
    return base


def _apply_classify(rule: Rule, candidates: list, config: Config, catalog: Catalog,
                     outcome: RuleOutcome, detail_out: dict[str, str] | None = None) -> list:
    """detail_out, if given, is populated uuid -> classify detail text for kept
    candidates -- used by preview_sweep to show "why this matched" in the review UI.
    Not needed by run_sweep's normal apply path, so it's optional and unused there."""
    if not rule.classify:
        return candidates
    backend_name = rule.classify["backend"]
    backend = get_backend(backend_name)
    prompt = rule.classify["prompt"]
    threshold = rule.classify.get("threshold", 0.5)
    phash = prompt_hash(backend_name, _classify_model(config, backend_name), prompt)

    kept = []
    for photo in candidates:
        detail = ""
        cached = catalog.get_verdict(photo.uuid, rule.name, phash)
        if cached is not None:
            verdict, confidence, detail = cached
        else:
            try:
                result = backend.classify(photo, prompt, config)
            except ClassifyError:
                outcome.classify_errors += 1
                continue
            verdict, confidence = result.verdict, result.confidence
            detail = result.detail or ""
            catalog.put_verdict(photo.uuid, rule.name, backend_name, phash,
                                 verdict, confidence, detail)
        if verdict and confidence >= threshold:
            kept.append(photo)
            if detail_out is not None:
                detail_out[photo.uuid] = detail
    return kept


def _due(candidates: list, ages: dict, after_days: int) -> list:
    return [p for p in candidates if (ages.get(p.uuid) or 0) >= after_days]


def _apply_file_stage(rule: Rule, candidates: list, run_id: str, catalog: Catalog,
                       apply: bool, outcome: RuleOutcome) -> None:
    if not rule.file:
        return
    if not apply:
        outcome.filed = len(candidates)
        return
    by_uuid = {p.uuid: p for p in candidates}
    album_name = rule.file.get("album")
    keyword = rule.file.get("keyword")
    filed_uuids: set[str] = set()

    if album_name:
        # A rule's query re-matches the same still-eligible photo on every scheduled
        # run (e.g. screenshot=true never turns false), so without this check every
        # subsequent sweep would re-log an "album" action for an already-filed photo
        # -- and `undo` (which defaults to the most recent run) would then "reverse"
        # a filing that actually happened long ago. Leaf-name comparison, not a full
        # path match, since osxphotos' photo.albums exposes album names, not paths.
        leaf = album_name.rsplit("/", 1)[-1]
        not_yet_filed = [u for u, p in by_uuid.items() if leaf not in (p.albums or [])]
        if not_yet_filed:
            n_added, failed = albums.add_to_album(not_yet_filed, album_name)
            ok = [u for u in not_yet_filed if u not in failed]
            for u in ok:
                catalog.log_action(run_id, rule.name, u, "album", {"album": album_name})
                filed_uuids.add(u)
            outcome.action_errors += [f"album add failed for {u}" for u in failed]
        filed_uuids |= {u for u, p in by_uuid.items() if leaf in (p.albums or [])}

    if keyword:
        not_yet_tagged = [u for u, p in by_uuid.items() if keyword not in (p.keywords or [])]
        if not_yet_tagged:
            n_updated, failed = keywords.set_keyword(not_yet_tagged, keyword)
            ok = [u for u in not_yet_tagged if u not in failed]
            for u in ok:
                catalog.log_action(run_id, rule.name, u, "keyword", {"keyword": keyword})
                filed_uuids.add(u)
            outcome.action_errors += [f"keyword set failed for {u}" for u in failed]
        filed_uuids |= {u for u, p in by_uuid.items() if keyword in (p.keywords or [])}

    outcome.filed = len(filed_uuids) if (album_name or keyword) else 0


def _apply_hide_stage(rule: Rule, candidates: list, ages: dict, run_id: str,
                       catalog: Catalog, apply: bool, outcome: RuleOutcome) -> None:
    if not rule.hide:
        return
    due = _due(candidates, ages, rule.hide.after_days)
    # Already-hidden photos stay "due" forever (age only grows), so without this
    # filter a photo would get a fresh "hide" action logged on every scheduled sweep
    # -- and `undo` (defaulting to the most recent run) would then unhide a photo
    # that was legitimately hidden long ago, not something this run actually did.
    # Filtered before the dry-run branch too, so a dry run's count matches what
    # --apply would actually do.
    due = [p for p in due if not getattr(p, "hidden", False)]
    # iCloud-only assets (osxphotos ismissing=True) can be hidden via PhotoKit, but
    # afterwards fetchAssetsWithLocalIdentifiers often stops returning them — even
    # with includeHiddenAssets — so undo/unhide fails. Skip until the original is
    # local; safer than a hide we can't reverse programmatically.
    missing = [p for p in due if getattr(p, "ismissing", False)]
    due = [p for p in due if not getattr(p, "ismissing", False)]
    if missing:
        outcome.action_errors.append(
            f"{len(missing)} photo(s) skipped hide — originals not downloaded from iCloud "
            f"(hide would succeed but unhide often cannot find them again)"
        )
    if not apply:
        outcome.hidden = len(due)
        return
    if not due:
        return
    try:
        results = hide_action.hide_photos([p.uuid for p in due])
    except Exception as e:
        # hide_photos raises (rather than returning per-uuid errors) only for a
        # total-auth failure -- not authorized to manage Photos at all. Don't let
        # that abort the whole sweep; every other rule/stage should still run.
        outcome.action_errors.append(f"hide stage skipped: {e}")
        return
    for uuid, status in results.items():
        if status == "ok":
            catalog.log_action(run_id, rule.name, uuid, "hide", {})
            outcome.hidden += 1
        else:
            outcome.action_errors.append(f"hide failed for {uuid}: {status}")


def _apply_archive_stage(rule: Rule, candidates: list, ages: dict, run_id: str,
                          config: Config, catalog: Catalog, apply: bool,
                          outcome: RuleOutcome) -> None:
    if not rule.archive:
        return
    due = _due(candidates, ages, rule.archive.after_days)
    if not apply:
        outcome.archived = len(due)
        return
    if not due:
        return
    if not config.backup:
        outcome.action_errors.append(
            f"{len(due)} photo(s) due for archive but [global].backup is not configured in rules.toml"
        )
        return
    for photo in due:
        # Check the catalog's own verified record, not just a bare filesystem
        # is_up_to_date() -- that used to skip straight past record_archive, so a
        # catalog reset (with the external backup volume otherwise untouched) left
        # those photos permanently unrecorded and confirm-deletes would report a
        # false MISSING backup forever. archive_photo() has its own cheap internal
        # idempotency check (stat + re-hash, no re-export) that DOES call through to
        # record_archive, so falling through to it also self-heals that case.
        if catalog.is_archived_and_verified(photo.uuid):
            outcome.archived += 1
            continue
        result = export_action.archive_photo(photo, config.backup)
        if result.ok:
            catalog.record_archive(result.uuid, result.path, result.sha256, result.nbytes, result.verified)
            catalog.log_action(run_id, rule.name, photo.uuid, "archive", {"path": result.path})
            outcome.archived += 1
        else:
            outcome.action_errors.append(f"archive failed for {photo.uuid}: {result.error}")


def _apply_delete_stage(rule: Rule, candidates: list, ages: dict, run_id: str,
                         catalog: Catalog, apply: bool, outcome: RuleOutcome) -> None:
    if not rule.delete:
        return
    due = _due(candidates, ages, rule.delete.after_days)
    outcome.staged_deletes = len(due)
    if not apply or not due:
        return
    # Only stage+log uuids not already staged -- staging again is harmless (staged_deletes
    # is keyed by uuid) but re-logging the "stage_delete" *action* every run would let
    # `undo` (defaulting to the most recent run) unstage a photo that was staged long
    # ago, not something this run did.
    already_staged = {row["uuid"] for row in delete_action.list_staged(catalog)}
    new_uuids = [p.uuid for p in due if p.uuid not in already_staged]
    if not new_uuids:
        return
    delete_action.stage_for_delete(catalog, run_id, rule.name, new_uuids)
    for uuid in new_uuids:
        catalog.log_action(run_id, rule.name, uuid, "stage_delete", {})


def _historically_claimed_uuids(catalog: Catalog, rule_names: set[str]) -> set[str]:
    """uuids ever filed (album or keyword action, not undone) by any of rule_names, in
    ANY prior run -- not just uuids claimed earlier in this same run_sweep() call.
    Without this, exclude_matched_by only works when the excluded rules happen to run
    in the same process (e.g. a full `sweep --apply`); a single-rule invocation like
    `haymish sweep screenshots-general --apply` would otherwise see an empty
    same-run claim set and re-file photos another rule already claimed previously."""
    actions = catalog.recent_actions(actions=["album", "keyword"], limit=200_000)
    return {a["uuid"] for a in actions if a["rule"] in rule_names}


def _rules_for(config: Config, rule_names: list[str] | None) -> list[Rule]:
    rules = [r for r in config.rules if r.enabled]
    if rule_names:
        wanted = set(rule_names)
        rules = [r for r in rules if r.name in wanted]
    return rules


def _select_and_classify(rule: Rule, photos: list, by_uuid: dict, now, config: Config,
                          catalog: Catalog, claimed: dict[str, str], outcome: RuleOutcome,
                          detail_out: dict[str, str] | None = None,
                          extra_exclude: set[str] | None = None) -> list:
    """Shared by run_sweep and preview_sweep: query -> exclude_matched_by -> classify.
    Stops short of any lifecycle stage. extra_exclude additionally drops uuids the
    caller already knows should never resurface (e.g. review-rejected)."""
    candidates = _select_candidates(rule, photos, by_uuid, now)
    if rule.exclude_matched_by:
        excluded = set(rule.exclude_matched_by)
        historical = _historically_claimed_uuids(catalog, excluded)
        candidates = [p for p in candidates
                      if claimed.get(p.uuid) not in excluded and p.uuid not in historical]
    if extra_exclude:
        candidates = [p for p in candidates if p.uuid not in extra_exclude]
    candidates = _apply_classify(rule, candidates, config, catalog, outcome, detail_out=detail_out)
    for p in candidates:
        claimed.setdefault(p.uuid, rule.name)
    return candidates


def preview_sweep(config: Config, catalog: Catalog, photosdb,
                   rule_names: list[str] | None = None) -> list[RulePreview]:
    """The query -> exclude -> classify phase only, with per-photo detail, for the
    `haymish review` browser UI to render before anything is actually applied.
    Report-only rules (nothing to confirm -- they never act) and rules with no
    lifecycle stage at all are skipped; `scan` already covers pure reporting."""
    photos = library.all_photos(photosdb)
    by_uuid = {p.uuid: p for p in photos}
    now = dt.datetime.now(dt.timezone.utc)
    claimed: dict[str, str] = {}

    previews = []
    for rule in _rules_for(config, rule_names):
        if rule.report_only or not (rule.file or rule.hide or rule.archive or rule.delete):
            continue
        outcome = RuleOutcome(rule=rule.name, report_only=rule.report_only)
        rejected = catalog.rejected_uuids_for_rule(rule.name)
        detail: dict[str, str] = {}
        candidates = _select_and_classify(rule, photos, by_uuid, now, config, catalog, claimed,
                                           outcome, detail_out=detail, extra_exclude=rejected)
        if not candidates:
            continue
        preview_candidates = [
            PreviewCandidate(
                uuid=p.uuid,
                filename=getattr(p, "original_filename", None) or p.uuid,
                date=str(getattr(p, "date", "")),
                classify_detail=detail.get(p.uuid, ""),
            )
            for p in candidates
        ]
        previews.append(RulePreview(rule=rule, candidates=candidates, preview_candidates=preview_candidates))
    return previews


def run_sweep(config: Config, catalog: Catalog, photosdb, rule_names: list[str] | None = None,
              apply: bool = False, only_uuids: set[str] | None = None) -> SweepReport:
    """only_uuids restricts candidate selection to a specific set of photos (e.g. a
    freshly imported batch) without changing which rules run or how they're scored --
    useful so `haymish import` doesn't re-sweep the whole library on every import."""
    photos = library.all_photos(photosdb)
    if only_uuids is not None:
        photos = [p for p in photos if p.uuid in only_uuids]
    by_uuid = {p.uuid: p for p in photos}
    now = dt.datetime.now(dt.timezone.utc)
    claimed: dict[str, str] = {}

    run_id = catalog.start_run("sweep-apply" if apply else "sweep-dry-run")

    outcomes: list[RuleOutcome] = []
    for rule in _rules_for(config, rule_names):
        outcome = RuleOutcome(rule=rule.name, report_only=rule.report_only)

        # Honor review rejects here too — otherwise `sweep --apply` (and the
        # scheduled job) would still act on photos the user unchecked in review.
        rejected = catalog.rejected_uuids_for_rule(rule.name)
        candidates = _select_and_classify(
            rule, photos, by_uuid, now, config, catalog, claimed, outcome,
            extra_exclude=rejected,
        )
        outcome.matched = len(candidates)

        if not rule.report_only and candidates:
            ages = {p.uuid: library.photo_age_days(p, now) for p in candidates}
            _apply_file_stage(rule, candidates, run_id, catalog, apply, outcome)
            _apply_hide_stage(rule, candidates, ages, run_id, catalog, apply, outcome)
            _apply_archive_stage(rule, candidates, ages, run_id, config, catalog, apply, outcome)
            _apply_delete_stage(rule, candidates, ages, run_id, catalog, apply, outcome)

        outcomes.append(outcome)

    catalog.finish_run(run_id, {
        "apply": apply,
        "counts": {o.rule: o.matched for o in outcomes},
        "staged_deletes": sum(o.staged_deletes for o in outcomes),
    })

    return SweepReport(run_id=run_id, apply=apply, generated=now.isoformat(), outcomes=outcomes)


def apply_confirmed(config: Config, catalog: Catalog, previews: list[RulePreview],
                     selections: dict[str, set[str]]) -> SweepReport:
    """The other half of `haymish review`: given preview_sweep's candidates and which
    uuids the user actually checked, act on exactly that subset -- via the SAME
    per-stage functions run_sweep --apply uses, so there's no separate code path that
    could drift from what a normal sweep would have done. Unchecked candidates are
    recorded as rejected so they don't resurface in the next review or sweep."""
    now = dt.datetime.now(dt.timezone.utc)
    run_id = catalog.start_run("review-apply")

    outcomes: list[RuleOutcome] = []
    for rp in previews:
        rule = rp.rule
        confirmed_uuids = selections.get(rule.name, set())
        confirmed = [p for p in rp.candidates if p.uuid in confirmed_uuids]
        for p in rp.candidates:
            if p.uuid not in confirmed_uuids:
                catalog.reject_candidate(p.uuid, rule.name)

        outcome = RuleOutcome(rule=rule.name, report_only=rule.report_only)
        outcome.matched = len(confirmed)
        if confirmed:
            ages = {p.uuid: library.photo_age_days(p, now) for p in confirmed}
            _apply_file_stage(rule, confirmed, run_id, catalog, True, outcome)
            _apply_hide_stage(rule, confirmed, ages, run_id, catalog, True, outcome)
            _apply_archive_stage(rule, confirmed, ages, run_id, config, catalog, True, outcome)
            _apply_delete_stage(rule, confirmed, ages, run_id, catalog, True, outcome)
        outcomes.append(outcome)

    catalog.finish_run(run_id, {
        "apply": True,
        "counts": {o.rule: o.matched for o in outcomes},
        "staged_deletes": sum(o.staged_deletes for o in outcomes),
    })
    return SweepReport(run_id=run_id, apply=True, generated=now.isoformat(), outcomes=outcomes)
