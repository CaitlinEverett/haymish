"""Threshold tuning for `semantic` rules: sweep min_score over cached embeddings.

Tuning a rule's `semantic.min_score` normally means guess, re-run the sweep, eyeball
the result, guess again. That loop is slow for no good reason: the embeddings are
already in the catalog, so scoring the whole library costs one query embedding, and
re-scoring at *any* threshold after that costs nothing at all. This module scores
once and derives every row of the sweep from that single pass -- never re-embedding
per threshold.

The other half is that the catalog already holds real labels: every photo the user
unchecked in `haymish review` is an explicit "no, not this one" for that rule
(`review_rejected`). Those are known negatives. Counting them per threshold turns an
aesthetic judgement into a measurement -- a partial, biased one (see
`_suggest`'s docstring, which is blunt about exactly how biased), but a real one.

Deliberately read-only: nothing here writes to the catalog or to rules.toml. It
reports; the human edits the number.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import library
from .ai.ollama_client import AIError
from .ai.search import semantic_scores  # module-level so tests can monkeypatch it
from .catalog import Catalog
from .config import Config, Rule

# 0.20..0.60 by 0.05. Below ~0.20 cosine on these models is noise; above ~0.60 a
# query-shaped embedding rarely matches anything, so a wider default sweep would be
# mostly empty rows.
DEFAULT_THRESHOLDS: tuple[float, ...] = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)

SAMPLE_CAP = 12                 # total borderline photos shown, split above/below
MIN_MATCH_FRACTION = 0.25       # a "reasonable number" floor, relative to the widest row
APPLIED_ACTIONS = ("album", "keyword", "hide")


@dataclass
class ThresholdRow:
    threshold: float
    matched: int                        # photos at or above this score
    rejected_hits: int                  # of those, how many the user unchecked in review
    precision_estimate: float | None    # 1 - rejected_hits/matched, or None when matched == 0


@dataclass
class TuningReport:
    rule: str
    query: str                  # the semantic query text ("" when the rule has no semantic block)
    indexed: int                # photos in this rule's candidate pool that have an embedding
    rows: list[ThresholdRow]
    suggested: float | None     # see _suggest(); None whenever there is no precision signal
    samples: list[dict]         # borderline photos: uuid, score, filename, caption, side
    # Extras beyond the required API -- everything the numbers don't say out loud.
    pool: int = 0               # photos passing the rule's non-semantic query
    current_min_score: float | None = None
    notes: list[str] = field(default_factory=list)


def _clean_thresholds(thresholds) -> list[float]:
    if thresholds is None:
        return list(DEFAULT_THRESHOLDS)
    return sorted({round(float(t), 4) for t in thresholds})


def _empty(rule: Rule, note: str, *, query: str = "", pool: int = 0) -> TuningReport:
    return TuningReport(
        rule=rule.name, query=query, indexed=0, rows=[], suggested=None, samples=[],
        pool=pool, current_min_score=_current_min_score(rule), notes=[note],
    )


def _current_min_score(rule: Rule) -> float | None:
    if not rule.semantic:
        return None
    # Mirrors _apply_semantic's default so the report shows the threshold actually
    # in force, not "unset".
    return float(rule.semantic.get("min_score", 0.35))


def _suggest(rows: list[ThresholdRow], rejected_in_pool: int) -> tuple[float | None, list[str]]:
    """Highest-precision threshold that still keeps a reasonable number of photos.

    Heuristic, stated plainly:
      * "reasonable" = at least 25% of the matches at the *lowest* threshold in the
        sweep, and at least one photo. Without that floor the winner is always the
        strictest threshold that happens to match a single clean photo -- precision
        1.0 on a sample of one, which is not a recommendation, it's arithmetic.
      * Among rows clearing that floor, take the best precision estimate; ties go to
        the LOWER threshold, since at equal measured precision you may as well keep
        more photos.

    Where this is weak, and it is weak:
      * No rejections recorded for the rule => no precision signal at all. Every row
        reads 1.0 and the "best" is meaningless. In that case there is no suggestion
        and this returns None. A confident-looking number invented from zero labels
        would be worse than silence.
      * Rejections are censored data. The user could only reject photos that were
        shown to them, and photos are only shown above the min_score in force at the
        time. Below that historical threshold, rejected_hits is structurally 0 -- so
        low thresholds look artificially clean. Read low-threshold precision as
        "unmeasured", not "good".
      * Only false positives are measured. Nothing here can see the photos the rule
        *should* have matched and didn't, so recall is entirely invisible. Raising
        the threshold always looks free by this metric; it is not.
      * An uncheck in review is not necessarily "bad semantic match" -- it can mean
        "correct match, but don't touch this one". Treating it as a negative slightly
        overstates the rule's error rate.
    """
    if rejected_in_pool == 0:
        return None, [
            "No suggestion: nothing has been rejected in review for this rule, so every "
            "threshold scores a perfect (and meaningless) precision. Run `haymish review`, "
            "uncheck the wrong picks, then re-run tuning -- those unchecks are the labels."
        ]
    if not rows or rows[0].matched == 0:
        return None, ["No suggestion: nothing matched at any threshold in the sweep."]

    floor = max(1, int(rows[0].matched * MIN_MATCH_FRACTION))
    eligible = [r for r in rows if r.matched >= floor and r.precision_estimate is not None]
    if not eligible:
        return None, [
            f"No suggestion: no threshold kept at least {floor} photo(s) "
            f"({int(MIN_MATCH_FRACTION * 100)}% of the {rows[0].matched} matched at "
            f"{rows[0].threshold:.2f})."
        ]
    best = max(eligible, key=lambda r: (r.precision_estimate, -r.threshold))
    notes = [
        f"Suggestion = best measured precision among thresholds keeping >= {floor} photo(s). "
        f"Precision counts only review rejections, so it sees false positives and never "
        f"false negatives -- a higher threshold always looks free here, and isn't."
    ]
    return best.threshold, notes


def _samples(scored: list[tuple[object, float]], pivot: float, catalog: Catalog) -> list[dict]:
    """The photos straddling `pivot`, which is where the judgement call actually is.

    Anything scoring 0.7 is obviously in and anything at 0.1 is obviously out; the
    only interesting question is what sits within a hair of the line.
    """
    half = SAMPLE_CAP // 2
    ranked = sorted(scored, key=lambda ps: ps[1], reverse=True)
    above = [x for x in ranked if x[1] >= pivot][-half:]   # lowest scores still above
    below = [x for x in ranked if x[1] < pivot][:half]     # highest scores below
    out = []
    for photo, score, side in ([(p, s, "above") for p, s in above]
                               + [(p, s, "below") for p, s in below]):
        uuid = photo.uuid
        out.append({
            "uuid": uuid,
            "score": round(float(score), 4),
            "filename": getattr(photo, "original_filename", None) or uuid,
            "caption": catalog.get_caption(uuid) or "",
            "side": side,
        })
    return out


def tune_semantic(config: Config, catalog: Catalog, photos: list, rule: Rule,
                   thresholds: list[float] | None = None) -> TuningReport:
    """Score this rule's candidate pool once, then report every threshold at once.

    `photos` is the full library (as `library.all_photos` returns it); the pool is
    narrowed here by the rule's own non-semantic `query`, so the counts describe the
    rule as configured rather than the whole library.

    Never raises for the ordinary failure modes -- no semantic block, empty index,
    Ollama unreachable all come back as a report carrying a note that says so.
    """
    if not rule.semantic:
        return _empty(rule, f"Rule {rule.name!r} has no [rule.{rule.name}.semantic] block — "
                            f"there is no min_score to tune.")

    query = str(rule.semantic.get("query", ""))
    now = dt.datetime.now(dt.timezone.utc)
    pool = [p for p in photos if library.matches_query(p, rule.query, now)]

    notes: list[str] = []
    if rule.detector:
        notes.append(
            f"Rule also runs the {rule.detector!r} detector, which is NOT applied here — "
            f"the real rule will match a subset of these counts."
        )
    if rule.semantic.get("top"):
        notes.append(
            f"Rule caps results at top = {int(rule.semantic['top'])}; matched counts below "
            f"ignore that cap (it truncates after the threshold filter)."
        )

    try:
        scores = semantic_scores(config, catalog, query)
    except AIError as e:
        return _empty(rule, f"Could not embed the query ({e}) — is Ollama running?",
                      query=query, pool=len(pool))
    if not scores:
        return _empty(rule, "Nothing indexed for the current embed model — run `haymish index` "
                            "first; there is nothing to score against.",
                      query=query, pool=len(pool))

    scored = [(p, float(scores[p.uuid])) for p in pool if p.uuid in scores]
    if not scored:
        return _empty(rule, f"{len(pool)} photo(s) pass this rule's query but none of them are "
                            f"in the AI index — run `haymish index`.",
                      query=query, pool=len(pool))
    if len(scored) < len(pool):
        notes.append(f"{len(pool) - len(scored)} of {len(pool)} pooled photo(s) are missing from "
                     f"the AI index and are excluded from every count below.")

    rejected = catalog.rejected_uuids_for_rule(rule.name)
    rejected_in_pool = sum(1 for p, _ in scored if p.uuid in rejected)

    rows: list[ThresholdRow] = []
    for t in _clean_thresholds(thresholds):
        hits = [(p, s) for p, s in scored if s >= t]
        matched = len(hits)
        rejected_hits = sum(1 for p, _ in hits if p.uuid in rejected)
        rows.append(ThresholdRow(
            threshold=t,
            matched=matched,
            rejected_hits=rejected_hits,
            precision_estimate=(1.0 - rejected_hits / matched) if matched else None,
        ))

    suggested, suggest_notes = _suggest(rows, rejected_in_pool)
    notes.extend(suggest_notes)

    # Fall back to the middle of the sweep when there's no suggestion — the samples
    # are still the most useful thing on the page, so always show some.
    pivot = suggested if suggested is not None else rows[len(rows) // 2].threshold

    return TuningReport(
        rule=rule.name,
        query=query,
        indexed=len(scored),
        rows=rows,
        suggested=suggested,
        samples=_samples(scored, pivot, catalog),
        pool=len(pool),
        current_min_score=_current_min_score(rule),
        notes=notes,
    )


def rule_feedback(catalog: Catalog, rule_name: str) -> dict:
    """How often this rule's picks got kept vs. unchecked, across all past reviews.

    Counts distinct photos, not action rows: a `file` stage logs both an "album" and
    a "keyword" action for the same photo, and counting rows would double it.

    rejection_rate is rejected / (applied + rejected) — a rejected photo was never
    applied, so the two sets are disjoint and their union is "photos this rule
    surfaced and the user ruled on". None when the rule has no history at all.
    """
    actions = catalog.recent_actions(actions=list(APPLIED_ACTIONS), limit=200_000)
    applied_uuids = {a["uuid"] for a in actions if a["rule"] == rule_name}
    rejected_uuids = catalog.rejected_uuids_for_rule(rule_name)
    # A photo can't be both; if it somehow is (rejected later than it was applied),
    # count it as rejected so the rate errs toward "this rule needs attention".
    applied_uuids -= rejected_uuids
    applied, rejected = len(applied_uuids), len(rejected_uuids)
    total = applied + rejected
    return {
        "applied": applied,
        "rejected": rejected,
        "rejection_rate": (rejected / total) if total else None,
    }


def format_report(report: TuningReport) -> str:
    """A plain text table for the CLI. No color, no unicode box drawing — this gets
    read in a terminal and pasted into notes."""
    out: list[str] = []
    out.append(f'semantic tuning — rule "{report.rule}"')
    if report.query:
        out.append(f'  query:   "{report.query}"')
    if report.current_min_score is not None:
        out.append(f"  current: min_score = {report.current_min_score:.2f}")
    out.append(f"  pool:    {report.pool} photo(s) pass the rule's query, "
               f"{report.indexed} of them indexed")

    if report.rows:
        out.append("")
        out.append("  threshold   matched   rejected   precision")
        out.append("  ---------   -------   --------   ---------")
        for r in report.rows:
            precision = "       — " if r.precision_estimate is None else f"{r.precision_estimate:8.2f} "
            marks = []
            if report.current_min_score is not None and abs(r.threshold - report.current_min_score) < 1e-9:
                marks.append("current")
            if report.suggested is not None and abs(r.threshold - report.suggested) < 1e-9:
                marks.append("SUGGESTED")
            mark = ("  <- " + ", ".join(marks)) if marks else ""
            out.append(f"     {r.threshold:5.2f}   {r.matched:7d}   {r.rejected_hits:8d}   "
                       f"{precision}{mark}")

    out.append("")
    if report.suggested is None:
        out.append("  suggested: none — no precision signal (see notes)")
    else:
        row = next(r for r in report.rows if abs(r.threshold - report.suggested) < 1e-9)
        out.append(f"  suggested: min_score = {report.suggested:.2f} "
                   f"({row.matched} photo(s), {row.rejected_hits} previously rejected)")

    if report.samples:
        out.append("")
        out.append("  borderline photos (the judgement call lives here):")
        for s in report.samples:
            side = "+" if s["side"] == "above" else "-"
            caption = s["caption"].replace("\n", " ")
            if len(caption) > 60:
                caption = caption[:57] + "..."
            out.append(f"    {side} {s['score']:.3f}  {s['filename']:<24} {caption}")

    if report.notes:
        out.append("")
        for n in report.notes:
            out.append(f"  note: {n}")
    return "\n".join(out)
