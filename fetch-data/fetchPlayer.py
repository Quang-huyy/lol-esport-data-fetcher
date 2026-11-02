#!/usr/bin/env python3
"""Export Player belonging to leagues listed in a Leaguepedia CSV."""

import argparse
import csv
from html import unescape
import os
import time

from mwrogue.esports_client import AuthCredentials, EsportsClient

from data_helpers import load_leagues, set_stable_id

PAGE_SIZE = 500
RATE_LIMIT_SLEEP = 60
PLAYER_COLUMNS = (
    "Id",
    "IngameName",
    "Name",
    "Nationality",
    "Birthdate",
    "IsRetired",
)

def fetch_players(site, leagues):
    """Fetch player rows whose Name value appears in the given list."""
    escaped_leagues = [
        "'" + league.replace("\\", "\\\\").replace("'", "\\'") + "'"
        for league in leagues
    ]
    tournament_filter = ", ".join(escaped_leagues)
    fields = (
        "Players._pageName=Page,Players.Player=IngameName,Players.Name=Name,"
        "Players.Country=Country,Players.Nationality__full=Nationality,"
        "Players.Birthdate=Birthdate, Players.IsRetired=IsRetired"
    )
    rows = []
    offset = 0

    while True:
        while True:
            try:
                page = site.cargo_client.query(
                    tables="Players,PlayerLeagueHistory",
                    fields=fields,
                    join_on="Players.Player = PlayerLeagueHistory.Player",
                    where=f"PlayerLeagueHistory.League IN ({tournament_filter})",
                    order_by="Players.Id",
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
        print(f"Retrieved {len(rows)} players.")
        if len(page) < PAGE_SIZE:
            return rows

        offset += len(page)

def write_csv(rows, output_path):
    """Write player dictionaries to a CSV file, keeping one entry per player."""
    unique_rows = []
    seen = set()
    for row in rows:
        row = {
            field: unescape(value).replace("\xa0", " ")
            if isinstance(value, str)
            else value
            for field, value in row.items()
        }
        player_key = (row.get("IngameName") or "").strip().lower()
        if not player_key or player_key in seen:
            continue
        seen.add(player_key)
        unique_rows.append(row)
    
    for row in unique_rows:
        set_stable_id(
            row,
            "player",
            row.get("Page") or row.get("IngameName"),
        )
        row["Nationality"] = (row.get("Country") or row.get("Nationality") or "").strip()

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=PLAYER_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(unique_rows)

    print(f"Wrote {len(unique_rows)} rows to {output_path}")

def main():
    parser = argparse.ArgumentParser(
        description="Export players for leagues listed in a Leaguepedia CSV."
    )
    parser.add_argument(
        "--leagues",
        default=os.path.join("output", "leagues.csv"),
        help="input league CSV (default: ../output/leagues.csv)",
    )
    parser.add_argument(
        "--out",
        default=os.path.join("output", "players.csv"),
        help="CSV output path (default: ../output/players.csv)",
    )
    args = parser.parse_args()

    leagues = load_leagues(args.leagues)
    rows = fetch_players(
        EsportsClient("lol", credentials=AuthCredentials(user_file="me")),
        list(leagues)
    )
    write_csv(rows, args.out)


if __name__ == "__main__":
    main()
