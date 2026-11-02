#!/usr/bin/env python3
"""Export player champion statistics for tournaments listed in a Leaguepedia CSV."""

import argparse
import csv
import os
import time

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from mwrogue.esports_client import AuthCredentials, EsportsClient
from data_helpers import (
    load_champions,
    load_players,
    load_tournaments_fullname,
    load_worlds_tournament_ids,
    set_stable_id,
)

PAGE_SIZE = 500
PLAYER_BATCH_SIZE = 100
RATE_LIMIT_SLEEP = 60
PLAYER_CHAMP_STATS_COLUMNS = (
    "Id",
    "PlayerId",
    "TournamentId",
    "ChampionId",
    "GamePlayed",
    "PlayerWin",
    "Kill",
    "Death",
    "Assist",
    "Gold",
    "Cs",
)

STAT_COLUMNS = {
    "Kills": "Kill",
    "Deaths": "Death",
    "Assists": "Assist",
    "Gold": "Gold",
    "Cs": "Cs",
}


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

    if exact_names:
        escaped_names = [_escape_cargo_value(name) for name in exact_names]
        yield (
            f"UPPER(OverviewPage) IN ({', '.join(escaped_names)})",
            set(exact_names),
        )

    if names_by_prefix:
        prefix_filters = [
            f"UPPER(OverviewPage) LIKE {_escape_cargo_value(prefix + '%')}"
            for prefix in names_by_prefix
        ]
        matching_names = set().union(*names_by_prefix.values())
        yield f"({' OR '.join(prefix_filters)})", matching_names


worlds_assign = {
    '770a8f6f-781b-5713-81f3-b36940c8d553' : '01c94b60-f0b9-58a0-8da8-793cddb4834c',
    'ce2bcf2f-cd7e-519d-9695-9009be575746' : '01c94b60-f0b9-58a0-8da8-793cddb4834c',
    '2f812f67-0585-54d9-b1d4-1e7ef05eecd5': '90ee78af-6fa4-5ba8-9d00-1308f51530db',
    '6ac5f2b3-f819-5915-87b1-4e8e3ad641b9': '90ee78af-6fa4-5ba8-9d00-1308f51530db',
    'f1126cdd-0a7c-5a62-8a39-aae0c298228a': '10cb4527-daa3-536e-93eb-073e35a73a89',
    '7290bfeb-6d8b-5d27-bb45-7aceb402589e': '10cb4527-daa3-536e-93eb-073e35a73a89',
    'a640fe91-c51e-5a23-a9d4-ea60473b2aef': '093464dd-38b6-5ea1-9f81-8e1891674374',
    '2398b519-c16e-5837-afd6-3d6bbe788a72': '093464dd-38b6-5ea1-9f81-8e1891674374',
    'b7358484-b653-5b2e-b079-3fdcdf5eaf84' : '5b7f6ea3-ad06-539c-bc89-c0b87212bc2b',
    'f783a51d-2a31-53d0-9405-c09483e05d57' : '5b7f6ea3-ad06-539c-bc89-c0b87212bc2b',
    'd0f6eb5d-2e4c-57d2-b670-d5b2f7db252f' : '52a99852-cd0a-5401-9f89-0160569614b7',
    '66112d23-0402-5c6f-a916-eabf9ebc1cff' : '52a99852-cd0a-5401-9f89-0160569614b7',
    'c63c77ce-ab05-5c98-9bae-c219c9012b32' : '886ef8f8-90df-5576-b40c-d1a2858b7e1b',
    'cd6490d1-ef40-5094-b842-260ad4f61136' : '886ef8f8-90df-5576-b40c-d1a2858b7e1b',
    'fd21d49c-f199-55ec-b7b9-f28a4ee8275b' : '1e1f6b33-6a93-52e6-9ca7-a319f117a57b',
    '5c70d66c-5e1f-5c4e-917e-2d48cf48415d' : '1e1f6b33-6a93-52e6-9ca7-a319f117a57b',
    'dc5c44ea-9b73-507f-89e1-92099fe4b3b7' : '85f86e86-014c-53b2-b2fd-941d987d0683',
    'f3618fa9-33f6-5df3-9d17-7a0acdd30d85' : '85f86e86-014c-53b2-b2fd-941d987d0683',
}
def fetch_player_champ_stats(
    site,
    tournaments,
    players,
    on_batch=None,
    start_batch=1,
):
    """Fetch stats in player batches and process each completed batch."""
    if start_batch < 1:
        raise ValueError("start_batch must be at least 1")
    if not tournaments or not players:
        return []

    fields = (
        "_pageName=Page,OverviewPage=OverviewPage,Link=Link,Champion=Champion,Kills=Kills,Deaths=Deaths,"
        "Assists=Assists,Gold=Gold,CS=Cs,Pentakills=Pentakills,Team=Team,Tournament=Tournament,Role=Role, PlayerWin = PlayerWin"
    )
    rows = []
    first_player_index = (start_batch - 1) * PLAYER_BATCH_SIZE
    for batch_start in range(first_player_index, len(players), PLAYER_BATCH_SIZE):
        player_batch = players[batch_start:batch_start + PLAYER_BATCH_SIZE]
        batch_number = batch_start // PLAYER_BATCH_SIZE + 1
        batch_end = batch_start + len(player_batch)
        print(
            f"Fetching player batch {batch_number}: "
            f"players {batch_start + 1}-{batch_end} of {len(players)} "
            f"({player_batch[0]} through {player_batch[-1]})."
        )
        escaped_players = [
            "'" + player.replace("\\", "\\\\").replace("'", "\\'") + "'"
            for player in player_batch
        ]
        player_filter = ", ".join(escaped_players)
        batch_rows = []
        for tournament_where, matching_tournaments in _tournament_filters(tournaments):
            offset = 0
            while True:
                while True:
                    try:
                        page = site.cargo_client.query(
                            tables="ScoreboardPlayers",
                            fields=fields,
                            where=(
                                f"{tournament_where} "
                                f"AND UPPER(Link) IN ({player_filter})"
                            ),
                            order_by="OverviewPage,Link",
                            limit=PAGE_SIZE,
                            offset=offset,
                        )
                        break
                    except Exception as error:
                        message = str(error).lower()
                        if "ratelimit" not in message and "rate limit" not in message:
                            raise RuntimeError(
                                f"Stats query failed for player batch {batch_number} "
                                f"(players {batch_start + 1}-{batch_end} of {len(players)}, "
                                f"{player_batch[0]!r} through {player_batch[-1]!r}): {error}"
                            ) from error
                        print(f"Rate limited; retrying in {RATE_LIMIT_SLEEP} seconds.")
                        time.sleep(RATE_LIMIT_SLEEP)

                batch_rows.extend(
                    row
                    for row in page
                    if (row.get("OverviewPage") or "").strip().upper()
                    in matching_tournaments
                )
                print(
                    f"Fetched {len(batch_rows)} raw rows for player batch {batch_number}."
                )
                if len(page) < PAGE_SIZE:
                    break
                offset += len(page)

        if on_batch is None:
            rows.extend(batch_rows)
        else:
            try:
                on_batch(batch_rows, batch_number, batch_start)
            except Exception as error:
                raise RuntimeError(
                    f"Failed to process or save player batch {batch_number} "
                    f"(players {batch_start + 1}-{batch_end} of {len(players)}, "
                    f"{player_batch[0]!r} through {player_batch[-1]!r}): {error}"
                ) from error

    return rows

def parse_stat(value, field_name):
    """Parse an optional numeric stat and report malformed source values."""
    value = "" if value is None else str(value).strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Invalid {field_name} stat value: {value!r}") from error


def format_stat(value):
    """Format a numeric stat to at most one decimal place."""
    if value == "":
        return ""
    rounded_value = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if rounded_value == 0:
        return "0"
    return format(rounded_value.normalize(), "f")


def write_csv(
    rows,
    output_path,
    tournaments,
    players,
    champions,
    append=False,
):
    aggregated_rows = {}
    tournament_not_count, player_not_found_count, champion_not_found_count = 0,0,0
    tournament_not_found_set = set()
    player_not_found_set = set()
    champion_not_found_set = set()

    for row in rows:
        tournament_name = (row.get("OverviewPage") or "").strip().upper()
        player_name = (row.get("Link") or "").strip().upper()
        champion_name = (row.get("Champion") or "").strip().upper()
        tournament_id = tournaments.get(tournament_name)
        if tournament_id is not None:
            tournament_id = worlds_assign.get(tournament_id, tournament_id)
        player_id = players.get(player_name)
        champion_id = champions.get(champion_name)

        if tournament_id is None:
            tournament_not_count += 1
            tournament_not_found_set.add(tournament_name)
            continue  # Skip tournaments not found in the input CSV
        if player_id is None:
            player_not_found_count += 1
            player_not_found_set.add(player_name)
            continue  # Skip players not found in the input CSV
        if champion_id is None:
            champion_not_found_count += 1
            champion_not_found_set.add(champion_name)
            continue  # Skip champions not found in the input CSV

        key = (tournament_id, player_id, champion_id)
        aggregate = aggregated_rows.setdefault(
            key,
            {
                "games": 0,
                "wins": 0,
                "totals": {field: Decimal(0) for field in STAT_COLUMNS.values()},
                "counts": {field: 0 for field in STAT_COLUMNS.values()},
            },
        )
        aggregate["games"] += 1
        player_win_value = row.get("PlayerWin")
        player_win_text = (
            "" if player_win_value is None else str(player_win_value).strip()
        )
        if player_win_text.casefold() == "yes":
            aggregate["wins"] += 1
        elif player_win_text and player_win_text.casefold() != "no":
            player_win = parse_stat(player_win_text, "PlayerWin")
            if player_win not in (Decimal(0), Decimal(1)):
                raise ValueError(
                    f"Invalid PlayerWin stat value: {player_win_text!r}"
                )
            aggregate["wins"] += int(player_win)
        for source_field, output_field in STAT_COLUMNS.items():
            stat = parse_stat(row.get(source_field), source_field)
            if stat is not None:
                aggregate["totals"][output_field] += stat
                aggregate["counts"][output_field] += 1

    result_rows = []
    for (tournament_id, player_id, champion_id), aggregate in aggregated_rows.items():
        result_row = {
            "PlayerId": player_id,
            "TournamentId": tournament_id,
            "ChampionId": champion_id,
            "GamePlayed": aggregate["games"],
            "PlayerWin": aggregate["wins"],
        }
        set_stable_id(
            result_row,
            "player_champion_stats",
            tournament_id,
            player_id,
            champion_id,
        )
        for output_field in STAT_COLUMNS.values():
            count = aggregate["counts"][output_field]
            result_row[output_field] = format_stat(
                aggregate["totals"][output_field] / count if count else ""
            )
        result_rows.append(result_row)

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    mode = "a" if append else "w"
    encoding = "utf-8" if append else "utf-8-sig"
    with open(output_path, mode, newline="", encoding=encoding) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=PLAYER_CHAMP_STATS_COLUMNS,
            extrasaction="ignore",
        )
        if not append:
            writer.writeheader()
        writer.writerows(result_rows)

    print(f"Wrote {len(result_rows)} rows to {output_path}")
    if champion_not_found_count > 0 or player_not_found_count > 0 or tournament_not_count > 0:
        print(f"Champions not found in input CSV: {champion_not_found_count}")
        for champion_name in champion_not_found_set:
            print(f" - {champion_name}")
        print(f"Players not found in input CSV: {player_not_found_count}")
        for player_name in player_not_found_set:
            print(f" - {player_name}")
        print(f"Tournaments not found in input CSV: {tournament_not_count}")
        for tournament_name in tournament_not_found_set:
            print(f" - {tournament_name}")
    return result_rows

def main():
    parser = argparse.ArgumentParser(
        description="Export player champion stats for tournaments listed in a Leaguepedia CSV."
    )
    parser.add_argument(
        "--tournaments",
        default=os.path.join("output", "tournaments.csv"),
        help="input tournament CSV (default: ../output/tournaments.csv)",
    )
    parser.add_argument(
        "--worlds-only",
        action="store_true",
        help=(
            "fetch only World Championship tournaments (Worlds, Play-In and Main Event); "
            "output defaults to output/player_champ_stats_worlds.csv"
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "CSV output path (default: ../output/player_champ_stats.csv, "
            "or output/player_champ_stats_worlds.csv with --worlds-only)"
        ),
    )
    parser.add_argument(
        "--players",
        default=os.path.join("output", "players.csv"),
        help="input player CSV (default: ../output/players.csv)",
    )
    parser.add_argument(
        "--champions",
        default=os.path.join("output", "champions.csv"),
        help="input champion CSV (default: ../output/champions.csv)",
    )
    parser.add_argument(
        "--start-batch",
        type=int,
        default=1,
        help=(
            "player batch number to start from; values greater than 1 append "
            "to an existing output CSV (default: 1)"
        ),
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

    if args.out is None:
        args.out = os.path.join(
            "output",
            "player_champ_stats_worlds.csv" if args.worlds_only else "player_champ_stats.csv",
        )

    tournaments = load_tournaments_fullname(args.tournaments)
    if args.worlds_only:
        worlds_ids = load_worlds_tournament_ids(args.tournaments)
        tournaments = {
            name: tournament_id
            for name, tournament_id in tournaments.items()
            if tournament_id in worlds_ids
        }
        if not tournaments:
            raise ValueError(f"No Worlds tournaments found in {args.tournaments}")
        print(f"Worlds only: {len(worlds_ids)} tournaments.")
    champions = load_champions(args.champions)
    players = load_players(args.players)
    output_dir = os.path.dirname(args.out)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    if args.start_batch > 1:
        if not os.path.isfile(args.out):
            raise FileNotFoundError(
                f"Cannot resume at batch {args.start_batch}: "
                f"output file does not exist: {args.out}"
            )
        with open(args.out, newline="", encoding="utf-8-sig") as output_file:
            header = next(csv.reader(output_file), None)
        if tuple(header or ()) != PLAYER_CHAMP_STATS_COLUMNS:
            raise ValueError(
                f"Cannot resume: {args.out} does not have the expected "
                "player champion stats CSV header"
            )
    else:
        write_csv([], args.out, tournaments, players, champions)

    def write_batch(batch_rows, batch_number, batch_start):
        written_rows = write_csv(
            batch_rows,
            args.out,
            tournaments,
            players,
            champions,
            append=True,
        )
        print(
            f"Saved batch {batch_number} (players {batch_start + 1}-"
            f"{min(batch_start + PLAYER_BATCH_SIZE, len(players))}); "
            f"wrote {len(written_rows)} output rows."
        )

    fetch_player_champ_stats(
        EsportsClient("lol", credentials=AuthCredentials(user_file="me")),
        list(tournaments),
        list(players),
        on_batch=write_batch,
        start_batch=args.start_batch,
    )


if __name__ == "__main__":
    main()
