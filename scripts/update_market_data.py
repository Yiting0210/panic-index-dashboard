"""Append the latest closed sessions to data/market_data_clean.csv.

Fetches Yahoo Finance closes (QQQ, SMH, S&P 500, Dow, VIX) and the CNN Fear &
Greed history from the last complete row onward, merges them append-only,
validates the result, and rewrites the CSV only if every check passes.

Usage:
    python scripts/update_market_data.py            # update the CSV
    python scripts/update_market_data.py --dry-run  # report only
"""
import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest import fetch_all, last_settled_session, merge, read_raw, write_raw  # noqa: E402
from ingest.market_update import last_complete_date  # noqa: E402

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "market_data_clean.csv"


def run(csv_path=CSV_PATH, dry_run=False):
    fieldnames, rows = read_raw(csv_path)
    settled = last_settled_session()
    # Start a little before the last complete row so the window always
    # contains a real trading day to chain-link against.
    start = last_complete_date(rows) - timedelta(days=10)
    fetched = fetch_all(start, settled)
    new_rows, report = merge(rows, fetched)
    if report.changed and not dry_run:
        write_raw(csv_path, fieldnames, new_rows)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, help="Also write the JSON report here")
    args = parser.parse_args()

    report = run(args.csv, args.dry_run).as_dict()
    report["dry_run"] = args.dry_run
    text = json.dumps(report, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
