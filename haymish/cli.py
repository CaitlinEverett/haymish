"""Haymish CLI."""

from __future__ import annotations

import importlib.resources
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .paths import RULES_PATH, ensure_app_dirs

console = Console()


def _load_config():
    from .config import ConfigError, load_config

    try:
        return load_config()
    except ConfigError as e:
        console.print(f"[red]config error:[/red] {e}")
        sys.exit(1)


@click.group()
@click.version_option(__version__)
def main():
    """Clean up and categorize your Apple Photos library."""


@main.command()
@click.option("--force", is_flag=True, help="Overwrite an existing rules.toml.")
def init(force: bool):
    """Install the starter rules.toml to ~/.haymish/."""
    ensure_app_dirs()
    if RULES_PATH.exists() and not force:
        console.print(f"{RULES_PATH} already exists (use --force to overwrite).")
        return
    template = importlib.resources.files("haymish").joinpath("rules-template.toml").read_text()
    RULES_PATH.write_text(template)
    console.print(f"[green]Wrote[/green] {RULES_PATH} — edit it, then run [bold]haymish doctor[/bold].")


@main.command()
def doctor():
    """Check permissions, library access, and backends."""
    from . import doctor as doc

    config = None
    if RULES_PATH.exists():
        config = _load_config()
    else:
        console.print("[yellow]No rules.toml yet — run `haymish init`. Checking basics only.[/yellow]")

    checks = doc.run_all(config)
    failed = 0
    for ok, label, detail in checks:
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f" {mark} [bold]{label}[/bold] — {detail}")
        failed += 0 if ok else 1
    if failed:
        console.print(f"\n[red]{failed} check(s) need attention.[/red]")
        sys.exit(1)
    console.print("\n[green]All checks passed.[/green]")


@main.command()
@click.option("--report/--no-report", default=True, help="Write markdown+HTML report files.")
def scan(report: bool):
    """Read-only inventory: screenshots, selfies, receipts, dupes, junk, people."""
    from .catalog import Catalog
    from .detectors import ALL_SCAN_DETECTORS
    from .library import all_photos, load_photosdb
    from .report import console_summary, write_reports

    config = _load_config()
    catalog = Catalog()
    run_id = catalog.start_run("scan")

    with console.status(f"Loading Photos library (this can take a minute on large libraries)…"):
        photosdb = load_photosdb(config.library)
        photos = all_photos(photosdb)

    library_stats = {
        "photos": sum(1 for p in photos if not getattr(p, "ismovie", False)),
        "videos": sum(1 for p in photos if getattr(p, "ismovie", False)),
        "hidden": sum(1 for p in photos if getattr(p, "hidden", False)),
        "favorites": sum(1 for p in photos if getattr(p, "favorite", False)),
        "size_gb": round(sum(getattr(p, "original_filesize", 0) or 0 for p in photos) / 1_000_000_000, 1),
    }

    results = []
    for detect in ALL_SCAN_DETECTORS:
        with console.status(f"Running {detect.__module__.split('.')[-1]} detector…"):
            results.append(detect(photos))

    console_summary(console, library_stats, results)
    if report:
        path = write_reports(config.report_dir, library_stats, results)
        console.print(f"\nReport: [bold]{path}[/bold] (+ .html)")

    catalog.finish_run(run_id, {"library": library_stats, "counts": {r.name: r.count for r in results}})
    catalog.close()


def _print_sweep_report(report, apply_: bool) -> None:
    table = Table(title=f"Sweep {'(applied)' if apply_ else '(dry-run)'} — run {report.run_id}")
    table.add_column("Rule")
    table.add_column("Matched", justify="right")
    table.add_column("Filed", justify="right")
    table.add_column("Hidden", justify="right")
    table.add_column("Archived", justify="right")
    table.add_column("Staged deletes", justify="right")
    table.add_column("Classify errors", justify="right")
    for o in report.outcomes:
        label = f"{o.rule} (report-only)" if o.report_only else o.rule
        table.add_row(label, str(o.matched), str(o.filed), str(o.hidden),
                      str(o.archived), str(o.staged_deletes), str(o.classify_errors))
    console.print(table)
    for o in report.outcomes:
        for err in o.action_errors:
            console.print(f"  [red]{o.rule}:[/red] {err}")
    total_staged = sum(o.staged_deletes for o in report.outcomes)
    if total_staged and apply_:
        console.print(
            f"\n[bold]{total_staged}[/bold] photo(s) newly staged for deletion. "
            f"Nothing is deleted yet — run [bold]haymish archive[/bold] then "
            f"[bold]haymish confirm-deletes[/bold] when ready."
        )


@main.command()
@click.argument("rule", required=False)
@click.option("--apply", "apply_", is_flag=True, help="Actually perform actions (default: dry-run).")
def sweep(rule, apply_):
    """Run sweep rules against the library (dry-run unless --apply is passed).

    Never performs an actual deletion, even with --apply — the delete lifecycle
    stage only stages candidates. Run `haymish confirm-deletes` to finalize.

    Prefer `haymish review` for day-to-day use — it shows thumbnails and lets you
    uncheck false positives before anything is applied.
    """
    from .catalog import Catalog
    from .library import load_photosdb
    from .sweep import run_sweep

    config = _load_config()
    known = {r.name for r in config.rules}
    if rule and rule not in known:
        console.print(f"[red]Unknown rule:[/red] {rule!r}. Known rules: {sorted(known)}")
        sys.exit(1)

    catalog = Catalog()
    with console.status("Loading Photos library (this can take a minute on large libraries)…"):
        photosdb = load_photosdb(config.library)

    report = run_sweep(config, catalog, photosdb, rule_names=[rule] if rule else None, apply=apply_)
    catalog.close()

    _print_sweep_report(report, apply_)
    if not apply_:
        console.print(
            "\n[dim]Dry run — no changes made. "
            "Prefer [bold]haymish review[/bold] to confirm with thumbnails, "
            "or re-run with --apply to act blindly.[/dim]"
        )


@main.command()
@click.argument("rule", required=False)
@click.option("--no-open", is_flag=True, help="Print the URL but don't auto-open the browser.")
def review(rule, no_open):
    """Confirm matches in a local browser, then apply only what you leave checked.

    Opens a localhost page with thumbnails for every rule that would act. Uncheck
    anything that shouldn't happen — those (photo, rule) pairs are remembered and
    won't resurface. Apply uses the same stage code as `sweep --apply`.
    """
    from .catalog import Catalog
    from .library import load_photosdb
    from .review import run_review

    config = _load_config()
    known = {r.name for r in config.rules}
    if rule and rule not in known:
        console.print(f"[red]Unknown rule:[/red] {rule!r}. Known rules: {sorted(known)}")
        sys.exit(1)

    catalog = Catalog()
    with console.status("Loading Photos library (this can take a minute on large libraries)…"):
        photosdb = load_photosdb(config.library)

    console.print("[dim]Matching rules and building thumbnails…[/dim]")

    def on_ready(url: str) -> None:
        console.print(f"Review queue: [bold]{url}[/bold]")
        console.print("[dim]Uncheck false positives, then click Apply selected. Ctrl-C cancels.[/dim]")

    report = run_review(
        config,
        catalog,
        photosdb,
        rule_names=[rule] if rule else None,
        auto_open=not no_open,
        on_ready=on_ready,
    )
    catalog.close()

    if report is None:
        console.print("Nothing to review — no actionable matches (or you cancelled).")
        return

    _print_sweep_report(report, apply_=True)


@main.command()
@click.option("--no-captions", is_flag=True,
              help="Skip vision-LLM captions; index only Photos' own OCR text and labels (much faster).")
@click.option("--limit", type=int, default=None,
              help="Index at most N un-indexed photos this run (useful for a first taste).")
@click.option("--concurrency", type=int, default=None,
              help="How many captions to run in parallel (default: auto-sized to this Mac).")
@click.option("--reindex-captions", "reindex_captions", is_flag=True,
              help="Drop captions written by any other vision model first, so they're "
                   "regenerated with the model currently configured in rules.toml.")
def index(no_captions, limit, concurrency, reindex_captions):
    """Build the AI index: a caption + embedding per photo, cached locally.

    Powers `haymish find`, `haymish ask`, and `semantic = {…}` rules. Incremental —
    re-running only processes new photos. Everything stays on this Mac.
    """
    from . import hardware
    from .ai.indexer import index_photos
    from .ai.ollama_client import AIError
    from .catalog import Catalog
    from .library import all_photos, load_photosdb

    config = _load_config()
    catalog = Catalog()

    if reindex_captions:
        stale = {m: n for m, n in catalog.caption_models().items()
                 if m != config.ai_vision_model}
        cleared = sum(catalog.clear_captions(model) for model in stale)
        if cleared:
            which = ", ".join(f"{n:,} from {m}" for m, n in sorted(stale.items(),
                                                                   key=lambda kv: -kv[1]))
            console.print(f"Cleared [bold]{cleared:,}[/bold] stale caption(s) ({which}) — "
                          f"they'll be regenerated with {config.ai_vision_model}.")
        else:
            console.print(f"No stale captions — everything already came from "
                          f"{config.ai_vision_model}.")

    hw = hardware.detect()
    if no_captions:
        console.print(f"{hw.describe()} — captions disabled (OCR text and labels only)")
    else:
        workers = concurrency or hardware.recommended_caption_workers(hw)
        console.print(f"{hw.describe()} — captioning {workers} at a time")

    with console.status("Loading Photos library (this can take a minute on large libraries)…"):
        photosdb = load_photosdb(config.library)
        # Videos included: they're captioned/embedded from their poster frame,
        # so find/ask/semantic rules work on them too.
        photos = all_photos(photosdb)

    from rich.progress import Progress

    with Progress(console=console) as prog:
        tasks: dict = {}

        def on_progress(done, total, phase):
            if phase not in tasks:
                # Captions and embeddings are interleaved now, so one bar covers
                # both; the label must say which work is actually happening or it
                # reads as "captioning stalled" during a --no-captions run.
                labels = {"caption": "Captioning", "embed": "Embedding",
                          "index": "Embedding" if no_captions else "Captioning + embedding"}
                tasks[phase] = prog.add_task(labels.get(phase, "Indexing"), total=total)
            prog.update(tasks[phase], completed=done)

        try:
            stats = index_photos(config, catalog, photos, captions=not no_captions,
                                  limit=limit, progress=on_progress,
                                  concurrency=concurrency)
        except AIError as e:
            console.print(f"[red]{e}[/red]")
            catalog.close()
            sys.exit(1)

    catalog.close()
    console.print(
        f"Indexed [bold]{stats.embedded}[/bold] photo(s) "
        f"({stats.captioned} captioned, {stats.caption_skipped_no_image} without a local image, "
        f"{stats.caption_failed} caption failures); {stats.already_indexed} already up to date."
    )
    for err in stats.errors:
        console.print(f"  [yellow]{err}[/yellow]")


@main.command()
@click.argument("query")
@click.option("--top", "top_k", default=20, show_default=True, help="How many matches to show.")
@click.option("--album", "album_name", default=None,
              help="Open the review UI to file confirmed matches into this album.")
@click.option("--no-open", is_flag=True, help="With --album: print the review URL but don't open it.")
def find(query, top_k, album_name, no_open):
    """Semantic search over the AI index — e.g. `haymish find "whiteboard from the conference"`.

    Read-only by default. With --album, matches open in the review UI so you
    confirm exactly which ones get filed.
    """
    from .ai.ollama_client import AIError
    from .ai.search import index_coverage, semantic_scores, top_matches
    from .catalog import Catalog
    from .library import all_photos, load_photosdb

    config = _load_config()
    catalog = Catalog()
    with console.status("Loading Photos library (this can take a minute on large libraries)…"):
        photosdb = load_photosdb(config.library)
        photos = all_photos(photosdb)
    by_uuid = {p.uuid: p for p in photos}

    indexed, total = index_coverage(config, catalog, photos)
    if indexed == 0:
        console.print("[red]Nothing indexed yet — run `haymish index` first.[/red]")
        catalog.close()
        sys.exit(1)
    if indexed < total:
        console.print(f"[yellow]Index covers {indexed}/{total} photos — run `haymish index` to complete it.[/yellow]")

    try:
        scores = semantic_scores(config, catalog, query)
    except AIError as e:
        console.print(f"[red]{e}[/red]")
        catalog.close()
        sys.exit(1)

    matches = [(u, s) for u, s in top_matches(scores, top_k) if u in by_uuid]
    if not matches:
        console.print("No matches.")
        catalog.close()
        return

    if album_name is None:
        table = Table(title=f"Closest matches for {query!r}")
        table.add_column("Score", justify="right")
        table.add_column("File")
        table.add_column("Date")
        table.add_column("About")
        for uuid, score in matches:
            p = by_uuid[uuid]
            caption = (catalog.get_caption(uuid) or "").replace("\n", " ")[:70]
            date = getattr(p, "date", None)
            table.add_row(f"{score:.2f}", p.original_filename,
                          f"{date:%Y-%m-%d}" if date else "", caption)
        console.print(table)
        console.print("[dim]Add --album \"Some Album\" to file confirmed matches (opens review).[/dim]")
        catalog.close()
        return

    from .config import Rule
    from .review import run_review

    rule = Rule(name=f"find:{query[:40]}",
                semantic={"query": query, "min_score": 0.0, "top": top_k},
                file={"album": album_name})

    def on_ready(url: str) -> None:
        console.print(f"Review matches: [bold]{url}[/bold]")

    report = run_review(config, catalog, photosdb, auto_open=not no_open,
                        on_ready=on_ready, rules_override=[rule])
    catalog.close()
    if report is None:
        console.print("Nothing filed (no matches or cancelled).")
        return
    _print_sweep_report(report, apply_=True)


@main.command()
@click.argument("request")
@click.option("--save", "save_name", default=None, metavar="NAME",
              help="Also save the generated rule to rules.toml under this name, so it runs in future sweeps.")
@click.option("--no-open", is_flag=True, help="Print the review URL but don't auto-open the browser.")
def ask(request, save_name, no_open):
    """Describe a cleanup in plain language — e.g. `haymish ask "file my recipe screenshots into Recipes"`.

    A local LLM turns the request into a rule, shows you its interpretation, and
    every matched photo goes through the browser review before anything happens.
    Ask can file into albums, tag, and hide — it can never archive or delete.
    """
    from .ai.ollama_client import AIError
    from .ai.planner import plan_from_prompt, plan_to_toml
    from .catalog import Catalog
    from .library import all_photos, load_photosdb
    from .review import run_review

    config = _load_config()
    catalog = Catalog()
    with console.status("Loading Photos library (this can take a minute on large libraries)…"):
        photosdb = load_photosdb(config.library)
        photos = all_photos(photosdb)

    album_names = sorted({a for p in photos for a in (p.albums or [])})
    existing_rules = {r.name for r in config.rules}

    with console.status(f"Planning with {config.ai_planner_model}…"):
        try:
            plan = plan_from_prompt(config, request, existing_albums=album_names,
                                     existing_rule_names=existing_rules)
        except AIError as e:
            console.print(f"[red]Couldn't plan that:[/red] {e}")
            catalog.close()
            sys.exit(1)

    if save_name:
        plan.raw["name"] = save_name
        plan.rule.name = save_name

    console.print(f"\n[bold]Plan:[/bold] {plan.description}")
    r = plan.rule
    if r.query:
        console.print(f"  filter: {r.query}")
    if r.semantic:
        console.print(f"  content match: {r.semantic['query']!r} (min score {r.semantic.get('min_score', 0.35)})")
    if r.classify:
        console.print(f"  per-photo check: {r.classify['prompt']!r}")
    if r.file:
        console.print(f"  action: file → {r.file}")
    if r.hide:
        console.print(f"  action: hide after {r.hide.after_days} day(s)")
    console.print("[dim]Matching photos open in the browser for confirmation — nothing is applied until you approve.[/dim]\n")

    def on_ready(url: str) -> None:
        console.print(f"Review: [bold]{url}[/bold]")

    report = run_review(config, catalog, photosdb, auto_open=not no_open,
                        on_ready=on_ready, rules_override=[plan.rule])
    catalog.close()

    if report is None:
        console.print("No photos matched (or you cancelled) — nothing applied.")
    else:
        _print_sweep_report(report, apply_=True)

    if save_name:
        block = plan_to_toml(plan)
        with open(config.source_path, "a") as f:
            f.write("\n" + block)
        console.print(f"[green]Saved[/green] rule [bold]{save_name}[/bold] to {config.source_path} — "
                      f"it now runs in every sweep (edit or delete it there any time).")


@main.command("confirm-deletes")
@click.option("--no-backup-i-understand", "skip_backup_check", is_flag=True,
              help="Proceed even for photos with no verified backup copy. Not recommended — "
                   "deletion beyond Photos' 30-day Recently Deleted window becomes permanent.")
def confirm_deletes(skip_backup_check):
    """Review and confirm staged deletions.

    This is the only command that can actually delete a photo, and even here the
    macOS system confirmation dialog is the final gate — it cannot be scripted past.
    Deletion always requires a verified backup copy first (run `haymish archive`),
    unless explicitly overridden, and always requires typing a confirmation phrase
    naming the exact count of photos about to be deleted.
    """
    from .actions import delete as delete_action
    from .actions.export import reverify_on_disk
    from .catalog import Catalog
    from .library import all_photos, load_photosdb

    config = _load_config()
    catalog = Catalog()

    staged = delete_action.list_staged(catalog)
    if not staged:
        console.print("Nothing staged for deletion.")
        catalog.close()
        return

    with console.status("Loading Photos library to resolve filenames…"):
        photosdb = load_photosdb(config.library)
    by_uuid = {p.uuid: p for p in all_photos(photosdb)}

    missing_backup = []
    table = Table(title=f"{len(staged)} photo(s) staged for permanent deletion")
    table.add_column("File")
    table.add_column("Rule")
    table.add_column("Staged")
    table.add_column("Backup")
    with console.status("Re-verifying backup copies on disk…"):
        for row in staged:
            photo = by_uuid.get(row["uuid"])
            filename = photo.original_filename if photo else f"[missing from library: {row['uuid']}]"
            archived = catalog.get_archive(row["uuid"])
            # Re-check the file on disk right now, not just the DB's verified_at flag
            # from whenever it was archived -- a backup drive can be unplugged,
            # reformatted, or the file corrupted/deleted since then with no trace in
            # catalog.db, which lives on the internal disk independent of the backup.
            verified = bool(
                archived and archived["verified_at"]
                and reverify_on_disk(archived["path"], archived["sha256"])
            )
            if not verified:
                missing_backup.append(row["uuid"])
            table.add_row(filename, row["rule"], row["staged_at"][:10],
                          "✓" if verified else "[red]MISSING[/red]")
    console.print(table)

    if missing_backup and not skip_backup_check:
        console.print(
            f"\n[red]{len(missing_backup)} photo(s) have no verified backup copy.[/red] "
            f"Run [bold]haymish archive[/bold] first, or pass --no-backup-i-understand "
            f"to proceed anyway (not recommended)."
        )
        catalog.close()
        sys.exit(1)

    console.print(
        f"\n[bold red]This will PERMANENTLY delete {len(staged)} photo(s).[/bold red] "
        f"They land in Photos' Recently Deleted for 30 days, then are gone for good. "
        f"Make sure you have an independent backup (external drive, USB stick) if this "
        f"matters to you beyond that window."
    )
    confirm_phrase = f"DELETE {len(staged)}"
    typed = console.input(f"Type [bold]{confirm_phrase}[/bold] to proceed: ")
    if typed.strip() != confirm_phrase:
        console.print("Confirmation text didn't match — aborted, nothing deleted.")
        catalog.close()
        sys.exit(1)

    console.print("\n[dim]macOS will now show its own confirmation dialog — approve it there to proceed.[/dim]")
    uuids = [row["uuid"] for row in staged]
    outcome = delete_action.confirm_and_delete(uuids)

    if outcome.cancelled:
        console.print("[yellow]Cancelled in the macOS dialog — nothing deleted.[/yellow]")
    elif outcome.error:
        console.print(f"[red]Delete failed:[/red] {outcome.error}")
    else:
        delete_action.unstage_all(catalog, outcome.deleted_uuids)
        run_id = catalog.start_run("confirm-deletes")
        for uuid in outcome.deleted_uuids:
            catalog.log_action(run_id, "confirm-deletes", uuid, "deleted", {})
        catalog.finish_run(run_id, {"deleted": len(outcome.deleted_uuids)})
        console.print(
            f"[green]Deleted {len(outcome.deleted_uuids)} of {outcome.requested} photo(s).[/green] "
            f"Recoverable from Photos' Recently Deleted for 30 days."
        )
        if len(outcome.deleted_uuids) < outcome.requested:
            console.print(
                f"[yellow]{outcome.requested - len(outcome.deleted_uuids)} staged uuid(s) were not "
                f"found in the library (already removed?) and remain staged — re-run "
                f"`haymish confirm-deletes` to clear them.[/yellow]"
            )

    catalog.close()


@main.command()
@click.option("--run-id", default=None, help="Undo a specific run instead of the most recent.")
def undo(run_id):
    """Reverse the album/keyword/hide/staged-delete actions of a sweep run (default: most recent).

    Archive actions are never undone (backups are a safety net, not something to
    delete), and confirmed deletions are never reversible through this tool.
    """
    from .catalog import Catalog
    from .undo import undo_run

    catalog = Catalog()
    report = undo_run(catalog, run_id=run_id)
    catalog.close()

    if not report.run_id:
        console.print(f"[yellow]{report.errors[0] if report.errors else 'nothing to undo'}[/yellow]")
        return

    console.print(f"Undid run [bold]{report.run_id}[/bold]:")
    if not report.reversed_counts:
        console.print("  nothing reversible in this run")
    for action, count in report.reversed_counts.items():
        console.print(f"  {action}: {count} reversed")
    if report.skipped_not_undoable:
        console.print(
            f"  [dim]{report.skipped_not_undoable} archive action(s) intentionally left in place "
            f"(undo doesn't delete backups)[/dim]"
        )
    if report.skipped_already_resolved:
        console.print(
            f"  [yellow]{report.skipped_already_resolved} staged-delete action(s) skipped[/yellow] "
            f"— already resolved (permanently deleted via confirm-deletes, or already unstaged)"
        )
    for err in report.errors:
        console.print(f"  [red]error:[/red] {err}")


@main.command()
def archive():
    """Export checksum-verified backup copies for every photo staged for deletion.

    Rules with an archive stage already get backed up during `sweep --apply` once
    they age past archive.after_days; this command guarantees backup coverage right
    now for whatever is currently staged, independent of that timing — run it before
    `haymish confirm-deletes`.
    """
    from .actions import delete as delete_action, export as export_action
    from .catalog import Catalog
    from .library import all_photos, load_photosdb

    config = _load_config()
    if not config.backup:
        console.print("[red]No [global].backup configured in rules.toml — nothing to archive to.[/red]")
        sys.exit(1)

    catalog = Catalog()
    staged = delete_action.list_staged(catalog)
    if not staged:
        console.print("Nothing staged for deletion — nothing to archive.")
        catalog.close()
        return

    with console.status("Loading Photos library…"):
        photosdb = load_photosdb(config.library)
    by_uuid = {p.uuid: p for p in all_photos(photosdb)}

    n_ok = n_skip = n_fail = 0
    for row in staged:
        photo = by_uuid.get(row["uuid"])
        if photo is None:
            n_fail += 1
            console.print(f"  [red]missing from library:[/red] {row['uuid']}")
            continue
        if catalog.is_archived_and_verified(row["uuid"]):
            n_skip += 1
            continue
        result = export_action.archive_photo(photo, config.backup)
        if result.ok:
            catalog.record_archive(result.uuid, result.path, result.sha256, result.nbytes, result.verified)
            n_ok += 1
        else:
            n_fail += 1
            console.print(f"  [red]failed:[/red] {photo.original_filename}: {result.error}")

    catalog.close()
    console.print(f"\nArchived {n_ok}, already up to date {n_skip}, failed {n_fail} (of {len(staged)} staged).")
    if n_fail:
        sys.exit(1)


@main.command("import")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--apply", "apply_", is_flag=True,
              help="Also run sweep rules against the imported photos (default: dry-run).")
@click.option("--album", "album_name", default=None, help="Import directly into this album.")
def import_(paths, apply_, album_name):
    """Import files into Photos, then run sweep rules against just the imported photos."""
    from photoscript import PhotosLibrary

    from .catalog import Catalog
    from .library import load_photosdb
    from .sweep import run_sweep

    config = _load_config()

    album = None
    if album_name:
        from .actions.albums import ensure_album
        try:
            album = ensure_album(album_name)
        except Exception as e:
            console.print(f"[red]Could not create album {album_name!r}:[/red] {e}")
            sys.exit(1)

    with console.status(f"Importing {len(paths)} file(s) into Photos…"):
        try:
            imported = PhotosLibrary().import_photos(list(paths), album=album, skip_duplicate_check=True)
        except Exception as e:
            console.print(f"[red]Import failed:[/red] {e}")
            sys.exit(1)

    uuids = [p.uuid for p in imported]
    console.print(f"[green]Imported {len(uuids)} of {len(paths)} file(s).[/green]")
    if not uuids:
        return

    catalog = Catalog()
    with console.status("Loading Photos library…"):
        photosdb = load_photosdb(config.library)

    report = run_sweep(config, catalog, photosdb, apply=apply_, only_uuids=set(uuids))
    catalog.close()
    _print_sweep_report(report, apply_)


@main.command()
@click.option("--interval-hours", default=24, show_default=True, type=int, help="How often to run.")
@click.option("--at-hour", "at_hour", type=int, default=None, metavar="INTEGER",
              help="Run daily at this hour (0-23) instead of on an interval — e.g. 3 for 3am.")
@click.option("--no-defer-when-busy", "no_defer_when_busy", is_flag=True,
              help="Run even when the Mac is busy or on battery (by default a scheduled "
                   "run skips those cycles and tries again at the next one).")
@click.option("--no-refresh-index", "no_refresh_index", is_flag=True,
              help="Skip `index` before each scheduled sweep — new photos won't be captioned/"
                   "embedded automatically, so find/ask/semantic rules will miss them until "
                   "you run `haymish index` yourself.")
@click.option("--uninstall", "do_uninstall", is_flag=True, help="Remove the scheduled job.")
@click.option("--status", "show_status", is_flag=True, help="Show whether it's installed/loaded.")
def schedule(interval_hours, at_hour, no_defer_when_busy, no_refresh_index, do_uninstall,
             show_status):
    """Install, remove, or check the launchd job that keeps Haymish current: refreshes
    the AI index (new photos since last run only — incremental) then runs `sweep --apply`.

    With --at-hour the job runs once a day at that hour. launchd doesn't fire while
    the Mac is asleep, so an overnight job runs on wake if its time already passed.

    Never schedules confirm-deletes — deletion always requires a human present.
    """
    from . import scheduler

    if show_status:
        st = scheduler.status()
        console.print(f"installed: {st['installed']}  loaded: {st['loaded']}")
        if st["installed"]:
            console.print(
                f"runs: {st['schedule']} — "
                f"{'index then sweep --apply' if st['refreshes_index'] else 'sweep --apply'}"
                f"{'' if st['defers_when_busy'] else ' (never defers)'}"
            )
        if st["log_tail"]:
            console.print(st["log_tail"])
        return

    if do_uninstall:
        scheduler.uninstall()
        console.print("Removed the scheduled job.")
        return

    if at_hour is not None and not 0 <= at_hour <= 23:
        console.print(f"[red]--at-hour must be between 0 and 23 (got {at_hour}).[/red]")
        sys.exit(1)

    try:
        scheduler.install(interval_hours=interval_hours, refresh_index=not no_refresh_index,
                          at_hour=at_hour, defer_when_busy=not no_defer_when_busy)
    except (RuntimeError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    steps = "`index` then `sweep --apply`" if not no_refresh_index else "`sweep --apply`"
    when = (f"daily at {at_hour}:00 (on wake, if the Mac was asleep then)"
            if at_hour is not None else f"every {interval_hours}h")
    gating = ("skipping any run that lands while the Mac is busy or on battery"
              if not no_defer_when_busy else "running even when the Mac is busy or on battery")
    console.print(
        f"[green]Scheduled[/green] — {steps} will run {when}, {gating} "
        f"(logs: ~/.haymish/scheduler.log). This never finalizes a deletion; run "
        f"`haymish confirm-deletes` yourself when you're ready."
    )


@main.command("scheduled-run")
@click.option("--force", is_flag=True,
              help="Run even if the Mac looks busy or is on battery.")
@click.option("--no-index", "no_index", is_flag=True,
              help="Sweep only — skip the index refresh.")
@click.pass_context
def scheduled_run(ctx, force, no_index):
    """What the launchd job invokes: index, then `sweep --apply`, unattended.

    Checks system load first and exits successfully without doing anything when
    the Mac is busy or on battery — deferring is a normal outcome, not a failure,
    so launchd doesn't treat it as a crashed job. Pass --force to run anyway.

    Like every unattended path in Haymish, this never deletes: sweep stages
    delete candidates at most, and only a human running `haymish confirm-deletes`
    can finalize them.
    """
    from . import scheduler

    if not force:
        reason = scheduler.should_defer()
        if reason:
            console.print(f"Deferring scheduled run — {reason}")
            return

    if not no_index:
        ctx.invoke(index)
    ctx.invoke(sweep, rule=None, apply_=True)


@main.command()
def menubar():
    """Run the menu-bar app (status, Review Now, Sweep Now, Confirm Deletes badge)."""
    from .menubar import main as menubar_main

    menubar_main()


@main.command()
@click.option("--port", default=None, type=int,
              help="Port to bind (default 8787; falls back to a free port if taken).")
def serve(port):
    """Run the Haymish daemon: dashboard at http://127.0.0.1:8787 plus the API the
    menu bar and MCP server use. Binds localhost only; mutations require the
    per-run token and always go through the browser review. No delete endpoint."""
    from .server import DEFAULT_PORT, serve as run_serve

    config = _load_config()
    console.print(f"[green]Haymish daemon starting[/green] — dashboard will be at "
                  f"http://127.0.0.1:{port or DEFAULT_PORT} (Ctrl-C to stop)")
    run_serve(config, port=port or DEFAULT_PORT)


@main.command()
def app():
    """Open the Haymish dashboard in your browser, starting the daemon if needed."""
    import subprocess

    from .server import ensure_daemon

    _load_config()  # fail fast with a good message if rules.toml is missing/broken
    with console.status("Starting Haymish…"):
        try:
            url, _token = ensure_daemon()
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)
    console.print(f"Dashboard: [bold]{url}/[/bold]")
    subprocess.run(["open", f"{url}/"], check=False)


@main.command()
def mcp():
    """Run the MCP server (stdio) so your AI can drive Haymish — read-only tools
    plus proposals that a human confirms in the browser review. Never deletes."""
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        console.print("[red]The mcp extra isn't installed — run `uv sync --extra mcp`.[/red]")
        sys.exit(1)
    mcp_main()


if __name__ == "__main__":
    main()
