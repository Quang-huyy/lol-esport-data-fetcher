"""Shared loaders for the Leaguepedia CSV and exchange-rate inputs."""

import csv
import json
import os
import re
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

WORLDS_NAME_PATTERN = re.compile(
    r"^Worlds (Season \d|\d{4})( Play-In| Main Event| Qualifier - .+)?$|^Worlds Qualifying Series( \d{4})?$"
)
EXCHANGE_RATES_PATH = os.path.join(os.path.dirname(__file__), "exchange_rates.json")
STABLE_ID_NAMESPACE = UUID("612f7808-8d3f-4f43-a5be-7c6803298c4e")


def _stable_id_name(entity, *parts):
    key = [
        entity,
        *(("" if part is None else str(part)).strip().casefold() for part in parts),
    ]
    return json.dumps(key, ensure_ascii=False, separators=(",", ":"))


def stable_uuid(entity, *parts):
    """Return a repeatable UUID for an entity's natural key."""
    return str(uuid5(STABLE_ID_NAMESPACE, _stable_id_name(entity, *parts)))


def set_stable_id(row, entity, *parts):
    """Add a deterministic UUID to an exported row."""
    row["Id"] = stable_uuid(entity, *parts)


def load_leagues(path):
    """Map league names to their CSV IDs."""
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not {"Id", "League"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} must have 'Id' and 'League' columns")

        leagues = {}
        for row in reader:
            name = (row.get("League") or "").strip().upper()
            league_id = (row.get("Id") or "").strip()
            if name and league_id:
                leagues[name] = league_id

    if not leagues:
        raise ValueError(f"No league names with IDs found in {path}")
    return leagues


def load_tournaments(path):
    """Map tournament names to their CSV IDs."""
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not {"Id", "Name"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} must have 'Id' and 'Name' columns")

        tournaments = {}
        for row in reader:
            name = (row.get("Name") or "").strip().upper()
            tournament_id = (row.get("Id") or "").strip()
            if name and tournament_id:
                tournaments[name] = tournament_id

    if not tournaments:
        raise ValueError(f"No tournament names found in {path}")
    return tournaments


def load_teams(path):
    """Map current and previous team names to their CSV IDs."""
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not {"Id", "Name"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} must have 'Id' and 'Name' columns")

        teams = {}
        previous_name_rows = []
        for row in reader:
            name = (row.get("Name") or "").strip().upper()
            team_id = (row.get("Id") or "").strip()
            if name and team_id:
                teams[name] = team_id
                previous_names_text = (row.get("PreviousNames") or "").strip()
                if previous_names_text:
                    try:
                        previous_names = json.loads(previous_names_text)
                    except json.JSONDecodeError as error:
                        raise ValueError(
                            f"Invalid PreviousNames JSON for team {name!r} in {path}"
                        ) from error
                    if not isinstance(previous_names, list) or not all(
                        isinstance(previous_name, str)
                        for previous_name in previous_names
                    ):
                        raise ValueError(
                            f"PreviousNames for team {name!r} in {path} must be "
                            "a JSON string list"
                        )
                    previous_name_rows.extend(
                        (previous_name.strip().upper(), team_id)
                        for previous_name in previous_names
                    )

    previous_name_ids = {}
    for previous_name, team_id in previous_name_rows:
        if previous_name:
            previous_name_ids.setdefault(previous_name, set()).add(team_id)

    for previous_name, team_ids in previous_name_ids.items():
        if len(team_ids) > 1:
            print(
                f"Warning: previous team name {previous_name!r} maps to multiple "
                f"team IDs in {path}; leaving it unresolved."
            )
            continue

        team_id = next(iter(team_ids))
        current_team_id = teams.get(previous_name)
        if current_team_id is not None and current_team_id != team_id:
            print(
                f"Warning: previous team name {previous_name!r} conflicts with a "
                f"current team in {path}; keeping the current team mapping."
            )
            continue
        teams[previous_name] = team_id

    if not teams:
        raise ValueError(f"No team names with IDs found in {path}")
    return teams


def load_worlds_tournament_ids(path):
    """IDs of World Championship tournaments (including Play-In and Main Event stages)."""
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        return {
            (row.get("Id") or "").strip()
            for row in csv.DictReader(csv_file)
            if WORLDS_NAME_PATTERN.match((row.get("Name") or "").strip())
        } - {""}


def load_tournaments_fullname(path):
    """Map tournament short and full names to their CSV IDs."""
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not {"Id", "Name"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} must have 'Id' and 'Name' columns")
        full_name_column = (
            "FullName" if "FullName" in reader.fieldnames else "Full Name"
        )

        tournaments = {}
        for row in reader:
            tournament_id = (row.get("Id") or "").strip()
            name = (row.get("Name") or "").strip().upper()
            full_name = (row.get(full_name_column) or "").strip().upper()
            if tournament_id:
                if name:
                    tournaments[name] = tournament_id
                if full_name:
                    tournaments[full_name] = tournament_id

    if not tournaments:
        raise ValueError(f"No tournament names with IDs found in {path}")
    return tournaments


def load_players(path):
    """Map player in-game names to their CSV IDs."""
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not {"Id", "IngameName"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} must have 'Id' and 'IngameName' columns")

        players = {}
        for row in reader:
            name = (row.get("IngameName") or "").strip().upper()
            player_id = (row.get("Id") or "").strip()
            if name and player_id:
                players[name] = player_id

    if not players:
        raise ValueError(f"No player names with IDs found in {path}")
    return players


def load_champions(path):
    """Map champion names to their CSV IDs."""
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not {"Id", "Name"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} must have 'Id' and 'Name' columns")

        champions = {}
        for row in reader:
            name = (row.get("Name") or "").strip().upper()
            champion_id = (row.get("Id") or "").strip()
            if name and champion_id:
                champions[name] = champion_id

    if not champions:
        raise ValueError(f"No champion names with IDs found in {path}")
    return champions


def load_usd_rates(currencies, path=EXCHANGE_RATES_PATH):
    """Load fixed USD conversion rates from a JSON file."""
    try:
        with open(path, encoding="utf-8") as rates_file:
            data = json.load(rates_file)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in exchange-rate file: {path}") from error

    if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
        raise ValueError(f"Exchange-rate file must contain a 'rates' object: {path}")

    rates = {}
    for currency, rate in data["rates"].items():
        try:
            decimal_rate = Decimal(str(rate))
        except InvalidOperation as error:
            raise ValueError(f"Invalid USD exchange rate for {currency}: {rate!r}") from error
        if not decimal_rate.is_finite() or decimal_rate <= 0:
            raise ValueError(f"Invalid USD exchange rate for {currency}: {rate!r}")
        rates[currency.upper()] = decimal_rate

    missing_currencies = sorted(set(currencies) - rates.keys())
    if missing_currencies:
        raise ValueError(
            f"Missing USD exchange rates in {path}: {', '.join(missing_currencies)}"
        )
    return rates
