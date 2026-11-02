#!/usr/bin/env python3
"""Export tournaments belonging to leagues listed in a Leaguepedia CSV."""

import argparse
import csv
from collections import deque
import json
import os
import time

from mwrogue.esports_client import AuthCredentials, EsportsClient
from data_helpers import (
    load_tournaments,
    set_stable_id,
)

PAGE_SIZE = 500
RATE_LIMIT_SLEEP = 60
TOURNAMENT_BATCH_SIZE = 500
TEAM_COLUMNS = (
    "Id",
    "Name",
    "Short",
    "Location",
    "Region",
    "IsDisbanded",
    "PreviousNames",
)


def _escape_cargo_value(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _tournament_filters(tournaments, field):
    exact_names = []
    names_by_prefix = {}
    for tournament in tournaments:
        name = tournament.strip().upper()
        if "#" not in name:
            exact_names.append(name)
            continue

        prefix = name.split("#", 1)[0]
        if not prefix:
            raise ValueError(
                f"Cannot safely query tournament with no prefix before '#': {name!r}"
            )
        names_by_prefix.setdefault(prefix, set()).add(name)

    for start in range(0, len(exact_names), TOURNAMENT_BATCH_SIZE):
        batch = exact_names[start:start + TOURNAMENT_BATCH_SIZE]
        escaped_names = [_escape_cargo_value(name) for name in batch]
        yield f"UPPER({field}) IN ({', '.join(escaped_names)})", set(batch)

    for prefix, names in names_by_prefix.items():
        pattern = _escape_cargo_value(prefix + "%")
        yield f"UPPER({field}) LIKE {pattern}", names


def _fetch_tournament_rows(
    site,
    tournaments,
    tables,
    fields,
    join_on,
    tournament_field,
    order_by,
    row_description,
):
    rows = []
    for where, matching_names in _tournament_filters(tournaments, tournament_field):
        offset = 0
        while True:
            while True:
                try:
                    page = site.cargo_client.query(
                        tables=tables,
                        fields=fields,
                        join_on=join_on,
                        where=where,
                        order_by=order_by,
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

            rows.extend(
                row
                for row in page
                if (row.get("Tournament") or "").strip().upper() in matching_names
            )
            print(f"Retrieved {len(rows)} {row_description}.")
            if len(page) < PAGE_SIZE:
                break

            offset += len(page)

    return rows


def fetch_teams(site, tournaments):
    """Fetch team rows whose Name value appears in the given list."""
    fields = (
        "T._pageName=Page,T.OverviewPage=Name,T.Short=Short,"
        "T.Location=Location,T.Region=Region,T.IsDisbanded=IsDisbanded,"
        "TR.Tournament=Tournament"
    )
    return _fetch_tournament_rows(
        site,
        tournaments,
        tables="Teams=T,TournamentRosters=TR",
        fields=fields,
        join_on="T.OverviewPage=TR.Team",
        tournament_field="TR.Tournament",
        order_by="T.Name,TR.Tournament",
        row_description="teams",
    )


def fetch_team_renames(site, tournaments):
    """Fetch all team rename records in pages for local lookup."""
    return _fetch_tournament_rows(
        site,
        tournaments,
        tables="TeamRenames, TournamentRosters",
        fields=(
            "OriginalName=OriginalName,NewName=NewName,IsSamePage=IsSamePage,"
            "TournamentRosters.Tournament=Tournament"
        ),
        join_on="TeamRenames.OriginalName=TournamentRosters.Team",
        tournament_field="TournamentRosters.Tournament",
        order_by="OriginalName,NewName,TournamentRosters.Tournament",
        row_description="team rename records",
    )


def get_previous_team_names(team_name, renames):
    """Walk back through every predecessor in the fetched rename records."""
    def normalize(name):
        return " ".join(name.split()).casefold()

    previous_by_name = {}
    for rename in renames:
        is_same_page = (rename.get("IsSamePage") or "").strip().casefold()
        if is_same_page != "yes":
            continue
        original = (rename.get("OriginalName") or "").strip()
        new = (rename.get("NewName") or "").strip()
        if original and new:
            previous_by_name.setdefault(normalize(new), []).append(original)

    previous_names = []
    seen = {normalize(team_name)}
    pending = deque([team_name])
    while pending:
        current = pending.popleft()
        for original in previous_by_name.get(normalize(current), []):
            original_key = normalize(original)
            if original_key not in seen:
                seen.add(original_key)
                previous_names.append(original)
                pending.append(original)
    return previous_names


def canonicalize_team_rows(rows, renames):
    """Keep the current row when a fetched team is listed as its predecessor."""
    def normalize(name):
        return " ".join(name.split()).casefold()

    unique_rows = []
    seen_pages = set()
    for source_row in rows:
        row = dict(source_row)
        team_key = (row.get("Page") or row.get("Name") or "").strip().casefold()
        if not team_key or team_key in seen_pages:
            continue
        seen_pages.add(team_key)
        name = (row.get("Name") or "").strip()
        previous_names = []
        existing_previous_names = (row.get("PreviousNames") or "").strip()
        if existing_previous_names:
            try:
                parsed_previous_names = json.loads(existing_previous_names)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid PreviousNames JSON for team {name!r}"
                ) from error
            if not isinstance(parsed_previous_names, list) or not all(
                isinstance(previous_name, str)
                for previous_name in parsed_previous_names
            ):
                raise ValueError(
                    f"PreviousNames for team {name!r} must be a JSON string list"
                )
            previous_names.extend(parsed_previous_names)
        if name:
            for previous_name in get_previous_team_names(name, renames):
                if normalize(previous_name) not in {
                    normalize(existing) for existing in previous_names
                }:
                    previous_names.append(previous_name)
        row["PreviousNames"] = json.dumps(previous_names, ensure_ascii=False)
        unique_rows.append(row)

    current_names = {
        normalize(row.get("Name") or "")
        for row in unique_rows
        if row.get("Name")
    }
    previous_name_owners = {}
    for row in unique_rows:
        current_name = normalize(row.get("Name") or "")
        previous_names = json.loads(row["PreviousNames"])
        for previous_name in previous_names:
            previous_key = normalize(previous_name)
            if previous_key in current_names and previous_key != current_name:
                previous_name_owners.setdefault(previous_key, set()).add(current_name)

    obsolete_names = set(previous_name_owners)
    return [
        row
        for row in unique_rows
        if normalize(row.get("Name") or "") not in obsolete_names
    ]


def write_csv(rows, output_path, renames=()):
    """Write team dictionaries to a CSV file, keeping one entry per team."""
    unique_rows = canonicalize_team_rows(rows, renames)

    for row in unique_rows:
        set_stable_id(row, "team", row.get("Page") or row.get("Name"))

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=TEAM_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(unique_rows)

    print(f"Wrote {len(unique_rows)} rows to {output_path}")

def main():
    parser = argparse.ArgumentParser(
        description="Export teams for leagues listed in a Leaguepedia CSV."
    )
    parser.add_argument(
        "--tournaments",
        default=os.path.join("output", "tournaments.csv"),
        help="input tournament CSV (default: ../output/tournaments.csv)",
    )
    parser.add_argument(
        "--out",
        default=os.path.join("output", "teams.csv"),
        help="CSV output path (default: ../output/teams.csv)",
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

    tournaments = load_tournaments(args.tournaments)
    site = EsportsClient("lol", credentials=AuthCredentials(user_file="me"))
    rows = fetch_teams(site, list(tournaments))
    renames = fetch_team_renames(site, list(tournaments))
    write_csv(rows, args.out, renames)


if __name__ == "__main__":
    main()
