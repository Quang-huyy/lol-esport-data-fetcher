#!/usr/bin/env python3
"""Export tournaments belonging to leagues listed in a Leaguepedia CSV."""

import argparse
import csv
import os
import time

from mwrogue.esports_client import AuthCredentials, EsportsClient
from data_helpers import load_leagues, set_stable_id

PAGE_SIZE = 500
RATE_LIMIT_SLEEP = 60
TOURNAMENT_COLUMNS = (
    "Id",
    "Name",
    "FullName",
    "TournamentDate",
    "LeagueId",
    "Region",
    "IsQualifier",
    "IsPlayoffs",
    "Year",
)

def fetch_tournaments(site, leagues):
    """Fetch tournament rows whose League value appears in the given list."""
    escaped_leagues = [
        "'" + league.replace("\\", "\\\\").replace("'", "\\'") + "'"
        for league in leagues
    ]
    fields = (
        "_pageName=Page,Name = Name, OverviewPage = FullName, DateStart = TournamentDate,"
        "Region = Region, League = League,"
        "IsQualifier = IsQualifier, IsPlayoffs = IsPlayoffs, Year = Year"
    )
    rows = []
    offset = 0

    while True:
        while True:
            try:
                page = site.cargo_client.query(
                    tables="Tournaments",
                    fields=fields,
                    where=f"UPPER(League) IN ({', '.join(escaped_leagues)})",
                    # Page name breaks DateStart ties so offset paging neither repeats nor skips rows.
                    order_by="DateStart,_pageName",
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
        print(f"Retrieved {len(rows)} tournaments.")
        if len(page) < PAGE_SIZE:
            return rows

        offset += len(page)

def write_csv(rows, leagues, output_path):
    """Write tournament dictionaries to a CSV file."""
    for row in rows:
        league_name = (row.get("League") or "").strip().upper()
        row["LeagueId"] = leagues.get(league_name, "")
        set_stable_id(
            row,
            "tournament",
            row.get("Page") or row.get("Name"),
        )
    rows = list({row["Id"]: row for row in rows}.values())
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=TOURNAMENT_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")

def main():
    parser = argparse.ArgumentParser(
        description="Export tournaments for leagues listed in a Leaguepedia CSV."
    )
    parser.add_argument(
        "--leagues",
        default=os.path.join("output", "leagues.csv"),
        help="input league CSV (default: ../output/leagues.csv)",
    )
    parser.add_argument(
        "--out",
        default=os.path.join("output", "tournaments.csv"),
        help="CSV output path (default: ../output/tournaments.csv)",
    )
    parser.add_argument(
        "--is-qualifier",
        default="No",
        help="IsQualifier filter (default: No)",
    )
    parser.add_argument(
        "--tournament-level",
        default="Primary",
        help="TournamentLevel filter (default: Primary)",
    )
    args = parser.parse_args()

    leagues = load_leagues(args.leagues)
    rows = fetch_tournaments(
        EsportsClient("lol", credentials=AuthCredentials(user_file="me")),
        list(leagues)
    )
    write_csv(rows, leagues, args.out)


if __name__ == "__main__":
    main()
