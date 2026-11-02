#!/usr/bin/env python3
"""Export team results for tournaments listed in a Leaguepedia CSV."""

import argparse
import csv
import os
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from mwrogue.esports_client import AuthCredentials, EsportsClient
from data_helpers import (
    load_teams,
    load_usd_rates,
    set_stable_id,
)

PAGE_SIZE = 500
RATE_LIMIT_SLEEP = 60
TOURNAMENT_BATCH_SIZE = 100
TEAM_RESULT_COLUMNS = (
    "Id",
    "TournamentId",
    "TeamId",
    "Placement",
    "Prize",
    "PrizeUnit",
)


def _escape_cargo_value(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _tournament_filters(tournaments):
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
        yield f"UPPER(Event) IN ({', '.join(escaped_names)})", set(batch)

    for prefix, names in names_by_prefix.items():
        pattern = _escape_cargo_value(prefix + "%")
        yield f"UPPER(Event) LIKE {pattern}", names


def load_primary_tournaments(tournaments_path, leagues_path):
    """Map tournament names to IDs when their league has Primary level."""
    with open(leagues_path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not {"Id", "Level"}.issubset(reader.fieldnames):
            raise ValueError(f"{leagues_path} must have 'Id' and 'Level' columns")

        primary_league_ids = {
            (row.get("Id") or "").strip()
            for row in reader
            if (row.get("Level") or "").strip().casefold() == "primary"
            and (row.get("Id") or "").strip()
        }

    if not primary_league_ids:
        raise ValueError(f"No primary-level league IDs found in {leagues_path}")

    with open(tournaments_path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not {"Id", "Name", "LeagueId"}.issubset(
            reader.fieldnames
        ):
            raise ValueError(
                f"{tournaments_path} must have 'Id', 'Name' and 'LeagueId' columns"
            )
        full_name_column = (
            "FullName" if "FullName" in reader.fieldnames else "Full Name"
        )

        tournaments = {}
        for row in reader:
            if (row.get("LeagueId") or "").strip() not in primary_league_ids:
                continue
            tournament_id = (row.get("Id") or "").strip()
            if not tournament_id:
                continue
            for column in ("Name", full_name_column):
                name = (row.get(column) or "").strip().upper()
                if name:
                    tournaments[name] = tournament_id

    if not tournaments:
        raise ValueError(
            f"No tournaments belonging to primary-level leagues found in "
            f"{tournaments_path}"
        )
    return tournaments


def fetch_teams_result(site, tournaments):
    """Fetch team results whose Event value appears in the given list."""
    fields = (
        "_pageName=Page,Event=Event,Tier=Tier,Showmatch=Showmatch,"
        "Place=Place,Place_Number=Place Number,Prize=Prize,PrizeUnit=PrizeUnit,Team=Team"
    )
    rows = []
    for where, matching_names in _tournament_filters(tournaments):
        offset = 0
        while True:
            while True:
                try:
                    page = site.cargo_client.query(
                        tables="TournamentResults",
                        fields=fields,
                        where=where,
                        order_by="Event,Team,Place_Number,PrizeUnit,Prize",
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
                if (row.get("Event") or "").strip().upper() in matching_names
            )
            print(f"Retrieved {len(rows)} team results.")
            if len(page) < PAGE_SIZE:
                break

            offset += len(page)

    return rows


def convert_prize_to_usd(prize, currency, rates):
    """Convert a prize with its ten-year average rate and round up to $1,000."""
    amount_text = (prize or "").strip()
    if not amount_text:
        return ""

    try:
        amount = Decimal(amount_text.replace(",", ""))
    except InvalidOperation as error:
        raise ValueError(f"Invalid prize amount: {prize!r}") from error
    if not amount.is_finite():
        raise ValueError(f"Invalid prize amount: {prize!r}")

    if currency not in rates:
        raise ValueError(f"No USD conversion rate available for prize unit {currency!r}")
    rounded_amount = (amount * rates[currency] / Decimal("1000")).to_integral_value(
        rounding=ROUND_CEILING
    ) * Decimal("1000")
    return str(rounded_amount.quantize(Decimal("1")))


def write_csv(rows, output_path, tournaments, teams):
    """Write tournament results with IDs and prizes converted to USD."""
    currencies = set()
    for row in rows:
        prize = (row.get("Prize") or "").strip()
        if prize:
            currency = (row.get("PrizeUnit") or "").strip().upper()
            if not currency:
                raise ValueError(f"Prize unit is missing for prize {prize!r}")
            currencies.add(currency)

    rates = load_usd_rates(currencies)
    result_rows = []
    team_not_found_count = 0
    team_not_found_set = set()
    duplicate_counts = defaultdict(int)
    for row in rows:
        tournament_name = (row.get("Event") or "").strip().upper()
        team_name = (row.get("Team") or "").strip().upper()
        tournament_id = tournaments.get(tournament_name)
        team_id = teams.get(team_name)
        if tournament_id is None:
            raise ValueError(f"Tournament not found in input CSV: {tournament_name!r}")
        if team_id is None:
            team_not_found_count += 1
            team_not_found_set.add(team_name)
            continue  # Skip teams not found in the input CSV

        currency = (row.get("PrizeUnit") or "").strip().upper()
        placement = row.get("Place Number") or row.get("Place") or ""
        converted_prize = convert_prize_to_usd(row.get("Prize"), currency, rates)
        prize_unit = "USD" if (row.get("Prize") or "").strip() else ""
        identity = (tournament_id, team_id, placement, prize_unit)
        duplicate_counts[identity] += 1
        result_row = {
            "TournamentId": tournament_id,
            "TeamId": team_id,
            "Placement": placement,
            "Prize": converted_prize,
            "PrizeUnit": prize_unit,
        }
        set_stable_id(
            result_row,
            "team_result",
            *identity,
            duplicate_counts[identity],
        )
        result_rows.append(result_row)

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=TEAM_RESULT_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(result_rows)

    print(f"Wrote {len(result_rows)} rows to {output_path}")
    if team_not_found_count > 0:
        print(f"Teams not found in input CSV: {team_not_found_count}")
        for team_name in team_not_found_set:
            display_name = team_name.encode("ascii", "backslashreplace").decode("ascii")
            print(f" - {display_name}")

def main():
    parser = argparse.ArgumentParser(
        description="Export team results for tournaments listed in a Leaguepedia CSV."
    )
    parser.add_argument(
        "--tournaments",
        default=os.path.join("output", "tournaments.csv"),
        help="input tournament CSV (default: ../output/tournaments.csv)",
    )
    parser.add_argument(
        "--leagues",
        default=os.path.join("output", "leagues.csv"),
        help="input league CSV used to select primary-level leagues",
    )
    parser.add_argument(
        "--out",
        default=os.path.join("output", "team_results.csv"),
        help="CSV output path (default: ../output/team_results.csv)",
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

    tournaments = load_primary_tournaments(args.tournaments, args.leagues)
    teams = load_teams(os.path.join("output", "teams.csv"))
    rows = fetch_teams_result(
        EsportsClient("lol", credentials=AuthCredentials(user_file="me")),
        list(tournaments)
    )
    write_csv(rows, args.out, tournaments, teams)


if __name__ == "__main__":
    main()
