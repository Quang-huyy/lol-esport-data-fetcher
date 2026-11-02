#!/usr/bin/env python3
"""Export primary Leaguepedia leagues to a CSV file."""

import argparse
import csv
import os
import time

from mwrogue.esports_client import AuthCredentials, EsportsClient
from data_helpers import set_stable_id

PAGE_SIZE = 500
RATE_LIMIT_SLEEP = 60
CHAMPION_COLUMNS = ("Id", "Name", "Title", "ReleaseDate")

def fetchChamps(site):
    fields = (
        "_pageName=Page,Name=Name,Title=Title,ReleaseDate=ReleaseDate"
    )

    rows = []
    offset = 0

    while True:
        while True:
            try:
                page = site.cargo_client.query(
                    tables="Champions",
                    fields=fields,
                    order_by="Name",
                    limit=PAGE_SIZE,
                    offset=offset,
                )
                break
            except Exception as error:
                message = str(error).lower()
                if "ratelimit" not in message and "rate limit" not in message:
                    raise
                print(f"Rate limited; retrying in {RATE_LIMIT_SLEEP} seconds.")
                time.sleep(RATE_LIMIT_SLEEP)

        rows.extend(page)
        print(f"Retrieved {len(rows)} rows.")
        if len(page) < PAGE_SIZE:
            return rows

        offset += len(page)

def write_csv(rows, output_path, columns):
    """Write dictionaries to a CSV file."""
    output_dir = os.path.dirname(output_path)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    for row in rows:
        set_stable_id(row, "champion", row.get("Page") or row.get("Name"))
    with open(output_path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")

def main():
    parser = argparse.ArgumentParser(
        description="Export primary Leaguepedia Champions rows to CSV."
    )
    parser.add_argument(
        "--out",
        default=os.path.join("output", "champions.csv"),
        help="CSV output path (default: ../output/champions.csv)",
    )
    args = parser.parse_args()
    credentials = AuthCredentials(user_file="me")
    site = EsportsClient('lol', credentials=credentials)
    rows = fetchChamps(site)
    write_csv(rows, args.out, CHAMPION_COLUMNS)


if __name__ == "__main__":
    main()
