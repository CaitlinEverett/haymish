#!/usr/bin/env python3
"""Unhide photos from the Haymish catalog hide ledger.

Usage (from Terminal.app — needs Photos TCC on Terminal):
  cd ~/dev/haymish && uv run python scripts/recover_hidden.py --run-id <run_id>
  uv run python scripts/recover_hidden.py 4BD24541-9964-40BF-9D0B-637E1775B3DD

Example UUID (from a hide test): 4BD24541-9964-40BF-9D0B-637E1775B3DD
"""
from __future__ import annotations

import argparse
import sys

from haymish.catalog import Catalog
from haymish.recover_hidden import recover_hidden


def main() -> int:
    parser = argparse.ArgumentParser(description="Unhide photos logged by Haymish hide actions.")
    parser.add_argument(
        "uuids",
        nargs="*",
        metavar="UUID",
        help="One or more photo UUIDs (otherwise use --run-id)",
    )
    parser.add_argument("--run-id", default=None, help="Unhide every hide action from this sweep run.")
    args = parser.parse_args()

    if not args.uuids and not args.run_id:
        parser.error("pass at least one UUID or --run-id (see help for an example UUID)")

    catalog = Catalog()
    try:
        report = recover_hidden(
            catalog,
            run_id=args.run_id,
            uuids=args.uuids if args.uuids else None,
        )
    finally:
        catalog.close()

    for err in report.errors:
        print(err, file=sys.stderr)
    if not report.uuids:
        return 1

    print(f"unhide {len(report.uuids)} uuid(s)… ok={report.ok} failed={len(report.failed)}")
    for uuid, status in report.failed:
        print(f"  {uuid}: {status}", file=sys.stderr)

    if report.errors and report.ok == 0:
        return 2
    return 0 if report.ok == len(report.uuids) else 2


if __name__ == "__main__":
    raise SystemExit(main())
