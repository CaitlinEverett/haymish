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
@click.option("--interval-hours", default=24, show_default=True, type=int, help="How often to sweep.")
@click.option("--uninstall", "do_uninstall", is_flag=True, help="Remove the scheduled sweep.")
@click.option("--status", "show_status", is_flag=True, help="Show whether it's installed/loaded.")
def schedule(interval_hours, do_uninstall, show_status):
    """Install, remove, or check the launchd job that runs `sweep --apply` periodically.

    Never schedules confirm-deletes — deletion always requires a human present.
    """
    from . import scheduler

    if show_status:
        st = scheduler.status()
        console.print(f"installed: {st['installed']}  loaded: {st['loaded']}")
        if st["log_tail"]:
            console.print(st["log_tail"])
        return

    if do_uninstall:
        scheduler.uninstall()
        console.print("Removed the scheduled sweep.")
        return

    try:
        scheduler.install(interval_hours=interval_hours)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    console.print(
        f"[green]Scheduled[/green] — `sweep --apply` will run every {interval_hours}h "
        f"(logs: ~/.haymish/scheduler.log). This never finalizes a deletion; run "
        f"`haymish confirm-deletes` yourself when you're ready."
    )


@main.command()
def menubar():
    """Run the menu-bar app (status, Review Now, Sweep Now, Confirm Deletes badge)."""
    from .menubar import main as menubar_main

    menubar_main()


if __name__ == "__main__":
    main()
