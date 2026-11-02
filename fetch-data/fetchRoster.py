import argparse
import csv
from collections import defaultdict
from datetime import date, datetime
import os
import time

from mwrogue.esports_client import AuthCredentials, EsportsClient
from data_helpers import (
    load_players,
    load_teams,
    set_stable_id,
)
PAGE_SIZE = 500
RATE_LIMIT_SLEEP = 60
ROSTER_COLUMNS = (
    "Id",
    "TeamId",
    "PlayerId",
    "JoinDate",
    "LeaveDate",
    "Role",
)

def format_date(value):
    """Format date and datetime values as YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return ""

    date_text = str(value).strip()
    if not date_text:
        return ""
    try:
        return datetime.fromisoformat(date_text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return date_text


def fetch_roster(site, teams, players):
    """Fetch roster rows for the given list of players."""
    if not players:
        return []

    escaped_player = [
        "'" + player.replace("\\", "\\\\").replace("'", "\\'") + "'"
        for player in players
    ]
    escaped_teams = [
        "'" + team.replace("\\", "\\\\").replace("'", "\\'") + "'" for team in teams]
    player_filter = ", ".join(escaped_player)
    team_filter = ", ".join(escaped_teams)
    fields = (
        "_pageName=Page, Date_Sort=Date Sort, Player=Player,"
        "Direction=Direction, Team=Team, Role=Role,"
        "RoleModifier=RoleModifier"
    )
    rows = []
    offset = 0

    while True:
        while True:
            try:
                page = site.cargo_client.query(
                    tables="RosterChanges",
                    fields=fields,
                    where=f"UPPER(Player) IN ({player_filter}) AND UPPER(Team) IN ({team_filter})",
                    order_by="Player, Date_Sort, Team",
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
        print(f"Retrieved {len(rows)} roster changes.")
        if len(page) < PAGE_SIZE:
            return rows

        offset += len(page)

def write_csv(rows, path, teams, players):
    """Pair explicit join and leave events without inventing intervals."""
    result = []
    grouped_rows = {}
    membership_occurrences = defaultdict(int)
    for row in rows:
        player_name = (row.get("Player") or "").strip().upper()
        team_name = (row.get("Team") or "").strip().upper()
        if player_name not in players or team_name not in teams:
            continue
        grouped_rows.setdefault((player_name, team_name), []).append(row)

    def append_membership(player_name, team_name, join, leave=None):
        join_event = join or {}
        leave_event = leave or {}
        team_id = teams[team_name]
        player_id = players[player_name]
        join_date = format_date(join_event.get("Date Sort"))
        leave_date = format_date(leave_event.get("Date Sort"))
        role = join_event.get("Role") or leave_event.get("Role") or ""
        role_modifier = (
            join_event.get("RoleModifier")
            or leave_event.get("RoleModifier")
            or ""
        ).strip()
        if role_modifier.casefold() == "trainee":
            return
        membership = {
            "TeamId": team_id,
            "PlayerId": player_id,
            "JoinDate": join_date,
            "LeaveDate": leave_date,
            "Role": role,
        }
        source_page = join_event.get("Page") or leave_event.get("Page")
        identity = (
            source_page or "composite",
            team_id,
            player_id,
            "join" if join_date else "leave",
            join_date or leave_date,
        )
        membership_occurrences[identity] += 1
        set_stable_id(
            membership,
            "roster_membership",
            *identity,
            membership_occurrences[identity],
        )
        result.append(membership)

    for (player_name, team_name), events in grouped_rows.items():
        ordered_events = sorted(
            enumerate(events),
            key=lambda indexed_event: (
                format_date(indexed_event[1].get("Date Sort")),
                0 if (indexed_event[1].get("Direction") or "").strip().casefold() == "leave" else 1,
                indexed_event[1].get("Page") or "",
                indexed_event[1].get("Role") or "",
                indexed_event[0],
            ),
        )
        pending_join = None
        closed_since_join = False
        for _, event in ordered_events:
            direction = (event.get("Direction") or "").strip().casefold()
            if direction == "join":
                if pending_join is not None:
                    append_membership(player_name, team_name, pending_join)
                pending_join = event
                closed_since_join = False
            elif direction == "leave":
                if pending_join is None:
                    if not closed_since_join:
                        append_membership(player_name, team_name, None, event)
                else:
                    append_membership(player_name, team_name, pending_join, event)
                    pending_join = None
                closed_since_join = True
            else:
                raise ValueError(
                    "Unexpected roster change direction "
                    f"{event.get('Direction')!r} for {player_name!r} at "
                    f"{event.get('Date Sort')!r}"
                )
        if pending_join is not None:
            append_membership(player_name, team_name, pending_join)

    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=ROSTER_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(result)

    print(f"Wrote {len(result)} rows to {path}")

def main():
    parser = argparse.ArgumentParser(
        description="Export roster information for players in a Leaguepedia CSV."
    )
    parser.add_argument(
        "--out",
        default=os.path.join("output", "roster.csv"),
        help="CSV output path (default: ../output/roster.csv)",
    )
    parser.add_argument(
        "--players",
        default=os.path.join("output", "players.csv"),
        help="input player CSV (default: ../output/players.csv)",
    )
    parser.add_argument(
        "--teams",
        default=os.path.join("output", "teams.csv"),
        help="input team CSV (default: ../output/teams.csv)",
    )
    args = parser.parse_args()

    players = load_players(args.players)
    teams = load_teams(args.teams)
    rows = fetch_roster(
        EsportsClient("lol", credentials=AuthCredentials(user_file="me")),
        list(teams),
        list(players),
    )
    write_csv(rows, args.out, teams, players)


if __name__ == "__main__":
    main()
