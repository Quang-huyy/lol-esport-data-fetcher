import pandas as pd
import argparse
import re
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from models import (playerChampStats, PlayerTitle, PlayerStatsDto, LeagueTitlesDto,
                    RegionTitlesDto, TournamentStatsDto, PlayerTournamentChampionStatsDto)
champ_df = pd.read_csv("output/champions.csv")
league_df = pd.read_csv("output/leagues.csv")
tournament_df = pd.read_csv("output/tournaments.csv")
team_df = pd.read_csv("output/teams.csv")
player_df = pd.read_csv("output/players.csv")
roster_df = pd.read_csv("output/roster.csv")
team_result_df = pd.read_csv("output/team_results.csv")
player_stats_df = pd.read_csv("output/player_champ_stats.csv")
image_df = pd.read_csv("output/images.csv")

image_urls = image_df.set_index("Id")["Url"].to_dict()

def getImageUrl(entity_id):
    # Entities without a real picture get a generated dicebear placeholder; treat as no image.
    url = image_urls.get(entity_id)
    if not isinstance(url, str) or not url.strip() or "dicebear.com" in url:
        return None
    return url

def getLeaguesByPlayer(player_ids):
    """League short names each player has recorded games in, so league questions stay fair."""
    stats = player_stats_df[player_stats_df["PlayerId"].isin(player_ids)]
    stats = stats[stats["GamePlayed"].fillna(0) > 0][["PlayerId", "TournamentId"]]
    tournaments = tournament_df[["Id", "LeagueId"]].rename(columns={"Id": "TournamentId"})
    leagues = league_df[["Id", "LeagueShort"]].rename(columns={"Id": "LeagueId"})
    merged = stats.merge(tournaments, on="TournamentId").merge(leagues, on="LeagueId")
    return {
        player_id: sorted(group["LeagueShort"].dropna().unique())
        for player_id, group in merged.groupby("PlayerId")
    }

def getPrimaryPlayerIds():
    """Players who ever played a game in a primary-level league (their full history is kept)."""
    primary_leagues = league_df.loc[league_df["Level"] == "Primary", "Id"]
    primary_tournaments = tournament_df.loc[tournament_df["LeagueId"].isin(primary_leagues), "Id"]
    stats = player_stats_df[
        player_stats_df["TournamentId"].isin(primary_tournaments)
        & (player_stats_df["GamePlayed"].fillna(0) > 0)
    ]
    return set(stats["PlayerId"])

primary_player_ids = getPrimaryPlayerIds()

def getPlayersByRegions(regions):
    stats = player_stats_df[["PlayerId", "TournamentId"]]
    stats = stats[stats["PlayerId"].isin(primary_player_ids)]
    tournaments = tournament_df[["Id", "LeagueId"]].rename(columns={"Id": "TournamentId"})
    leagues = league_df[["Id", "Region"]].rename(columns={"Id": "LeagueId"})

    matched = (
        stats
        .merge(tournaments, on="TournamentId", how="inner")
        .merge(leagues, on="LeagueId", how="inner")
    )
    matched = matched[matched["Region"].isin(regions)]
    player_ids = matched["PlayerId"].unique()
    return player_df[player_df["Id"].isin(player_ids)]\
        [["Id", "IngameName", "Nationality", "Birthdate"]]\
        .sort_values("IngameName").reset_index(drop=True)

def getPlayerStats(player_ids, regions):
    stats = player_stats_df[player_stats_df["PlayerId"].isin(player_ids)].copy()
    champions = champ_df[["Id", "Name"]].rename(columns={"Id": "ChampionId", "Name": "ChampionName"})
    tournaments = tournament_df[["Id", "Name", "LeagueId"]].rename(columns={"Id": "TournamentId", "Name": "TournamentName"})
    leagues = league_df[["Id", "Region"]].rename(columns={"Id": "LeagueId"})

    merged = (stats
        .merge(champions, on="ChampionId", how="inner")
        .merge(tournaments, on="TournamentId", how="inner")
        .merge(leagues, on="LeagueId", how="inner")
    )
    merged = merged[merged["Region"].isin(regions)].copy()

    # Nulls count as 0, like `?? 0` in the C# query.
    num_cols = ["GamePlayed", "PlayerWin", "Kill", "Death", "Assist", "Gold", "Cs"]
    merged[num_cols] = merged[num_cols].fillna(0)

    grouped = merged.groupby(
        ["PlayerId", "TournamentId", "TournamentName", "ChampionName"], as_index=False, dropna=False
    )[num_cols].sum()

    res = []
    for row in grouped.itertuples(index=False):
        deaths = row.Death if row.Death != 0 else 1
        res.append(playerChampStats(
            player_id=row.PlayerId,
            tournament_id=row.TournamentId,
            tournament_name=row.TournamentName,
            champion_name=row.ChampionName,
            games_played=row.GamePlayed,
            gameWon=row.PlayerWin,
            kills=row.Kill,
            deaths=row.Death,
            assists=row.Assist,
            gold=row.Gold,
            cs=row.Cs,
            kda=float((row.Kill + row.Assist) / deaths),
        ))
    return res

def _like(series, pattern, ignore_case=False):
    # SQL LIKE: % = any run of characters, _ = single character.
    regex = "^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$"
    flags = re.IGNORECASE if ignore_case else 0
    return series.fillna("").astype(str).str.match(regex, flags=flags)

def getPlayerTitles(player_ids, regions):
    international_names = (
        [f"Worlds {y}" for y in range(2014, 2027)]
        + [f"MSI {y}" for y in range(2015, 2027)]
        + [f"First Stand {y}" for y in range(2025, 2027)]
        + ["Worlds Season 1", "Worlds Season 2", "Worlds Season 3"]
    )
    # SEA splits that had no separate playoffs tournament.
    sea_no_playoffs_names = [
        "GPL 2014 Spring", "GPL 2014 Summer", "GPL 2014 Winter",
        "GPL 2016 Spring", "GPL 2016 Summer",
        "GPL 2017 Spring", "GPL 2017 Summer",
        "GPL 2018 Spring", "LST 2019 Spring",
    ]

    roster = roster_df[roster_df["PlayerId"].isin(player_ids)][
        ["PlayerId", "TeamId", "Role", "JoinDate", "LeaveDate"]]
    results = team_result_df[["TeamId", "TournamentId", "Placement"]]
    tournaments = tournament_df[
        ["Id", "Name", "FullName", "TournamentDate", "LeagueId", "Year", "IsPlayoffs"]
    ].rename(columns={"Id": "TournamentId", "Name": "TournamentName", "FullName": "TournamentFullName"})
    # Base Worlds rows (which hold the results) have no Year; take it from the name.
    worlds_year = pd.to_numeric(tournaments["TournamentName"].str.extract(r"^Worlds (\d{4})$")[0])
    tournaments["Year"] = tournaments["Year"].fillna(worlds_year)
    leagues =  league_df[["Id", "LeagueShort", "Region", "Level"]].rename(columns={"Id": "LeagueId"})
    teams = team_df[["Id", "Name"]].rename(columns={"Id": "TeamId", "Name": "TeamName"})

    df = (roster
        .merge(results, on="TeamId", how="inner")
        .merge(tournaments, on="TournamentId", how="inner")
        .merge(leagues, on="LeagueId", how="inner")
        .merge(teams, on="TeamId", how="inner")
    )

    role = df["Role"]
    name = df["TournamentName"]
    year = df["Year"]
    region = df["Region"]
    like = lambda p: _like(name, p)

    is_role = pd.Series(False, index=df.index)
    for r in ["%Mid%", "%Bot%", "%Top%", "%Jungle%", "%Support%"]:
        is_role |= _like(role, r, ignore_case=True)

    na_lcs = (
        ((year >= 2011) & (year <= 2018) & like("NA LCS%") & like("%Playoffs%"))
        | ((year >= 2019) & (year <= 2020)
            & (like("%LCS%") | like("%Regional Finals%") | like("%Playoffs%")))
        | ((year >= 2021) & (year <= 2024) & like("%LCS%")
            & (like("%Mid-Season Showdown%") | like("%Championship%") | like("%Spring Playoffs%")))
        | ((year == 2025)
            & (like("%LTA 2025 Split 1 Playoffs%") | like("%LTA North 2025 Split 2 Playoffs%")
                | like("%LTA 2025 Championship%")))
        | ((year == 2026) & like("%LCS%")
            & (like("%Lock-In%") | like("%Spring Playoffs%") | like("%Summer Playoffs%")))
    )
    europe = (
        ((year >= 2011) & (year <= 2018) & like("%EU LCS%") & like("%Playoffs%"))
        | ((year >= 2019) & (year <= 2026) & like("%LEC%")
            & (like("%Playoffs%") | like("%Season Finals%")))
    )
    korea = (
        ((year >= 2012) & (year <= 2014) & like("%Champions%"))
        | ((year >= 2015) & (year <= 2024) & like("%Playoffs%"))
        | ((year >= 2025) & (year <= 2026) & (like("%Season Playoffs%") | like("%LCK Cup%")))
    )
    brazil_americas = (
        ((region == "Brazil") & (like("CBLOL%Playoffs%") | like("Brazilian Champions Series%")))
        | like("LTA South%Playoffs%")
        | like("LTA 20__ Split 1 Playoffs")
        | like("LTA 20__ Championship")
    )
    international = (
        ((year >= 2011) & (year <= 2013)
            & name.isin(["Worlds Season 1", "Worlds Season 2", "Worlds Season 3"]))
        | name.isin(international_names)
        | like("Rift Rivals 2017 %") | like("Rift Rivals 2018 %") | like("Rift Rivals 2019 %")
    )

    handled_regions = [
        "Americas", "North America", "Europe", "China", "Korea", "Turkey", "Asia Pacific",
        "Japan", "Brazil", "Oceania", "Vietnam", "SEA", "CIS", "LMS", "Latin America",
        "LAN", "LAS", "International",
    ]
    is_title_tournament = (
        (region.isin(["Americas", "North America"]) & na_lcs)
        | ((region == "Europe") & europe)
        | ((region == "China") & like("%LPL%") & (like("%Playoffs%") | like("%Grand Finals%")))
        | ((region == "Korea") & korea)
        | ((region == "Turkey") & like("TCL%") & like("%Playoffs%"))
        | ((region == "Asia Pacific") & like("%Playoffs%") & ~like("%Wildcard%"))
        | ((region == "Japan")
            & (like("%Playoffs%") | like("%Grand Finals%") | (name == "LJL 2025 Finals")
                | ((year == 2026) & (df["IsPlayoffs"] == True))))
        | (region.isin(["Brazil", "Americas"]) & brazil_americas)
        | ((region == "Oceania") & like("%Playoffs%"))
        | ((region == "Vietnam")
            & (like("%Playoffs%") | (like("VCS%Finals%") & ~like("%Stage 1%"))))
        | ((region == "SEA") & (like("%Playoffs%") | name.isin(sea_no_playoffs_names)))
        | (region.isin(["CIS", "LMS", "Latin America", "LAN", "LAS"]) & like("%Playoffs%"))
        | ((region == "International") & international)
        | (~region.isin(handled_regions) & like("%Playoffs%"))
    )

    join_date = pd.to_datetime(df["JoinDate"], errors="coerce")
    leave_date = pd.to_datetime(df["LeaveDate"], errors="coerce")
    tournament_date = pd.to_datetime(df["TournamentDate"], errors="coerce")
    in_roster = (
        join_date.notna() & tournament_date.notna()
        & (join_date <= tournament_date)
        & (leave_date.isna() | (leave_date >= tournament_date))
    )

    mask = (
        role.notna() & is_role
        & (pd.to_numeric(df["Placement"], errors="coerce") == 1)
        & year.notna()
        & region.isin(regions)
        & (df["Level"] == "Primary")
        & is_title_tournament
        & in_roster
    )

    out = df[mask][
        ["PlayerId", "Region", "LeagueShort", "TeamName", "TournamentName",
         "TournamentFullName", "Year", "Role"]
    ].copy()
    out = out[
        ["PlayerId", "Region", "LeagueShort", "TeamName", "TournamentFullName", "Year", "Role"]
    ].drop_duplicates()
    return [
        PlayerTitle(
            player_id=r.PlayerId,
            region=r.Region,
            league_short=r.LeagueShort,
            team_name=r.TeamName if pd.notna(r.TeamName) else None,
            tournament_name=r.TournamentFullName if pd.notna(r.TournamentFullName) else None,
            tournament_year=int(r.Year),
            role=r.Role,
        )
        for r in out.itertuples(index=False)
    ]

PLAYING_ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]

def round1(value):
    # MidpointRounding.AwayFromZero on one decimal.
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))

def calculateAge(birthdate):
    born = pd.to_datetime(birthdate, errors="coerce")
    if pd.isna(born):
        return None
    born = born.date()
    today = datetime.now(timezone.utc).date()
    age = today.year - born.year
    if (born.month, born.day) > (today.month, today.day):
        age -= 1
    return age if age > 0 else None

def getPlayerRoles(player_ids):
    rows = roster_df[roster_df["PlayerId"].isin(player_ids) & roster_df["Role"].notna()]
    roles = {}
    for player_id, group in rows.groupby("PlayerId"):
        found = set()
        for role in group["Role"].drop_duplicates():
            for token in (t.strip().lower() for t in str(role).split("/")):
                found.update(r for r in PLAYING_ROLES if r.lower() == token)
        roles[player_id] = "/".join(r for r in PLAYING_ROLES if r in found)
    return roles

def summarize(rows):
    game_played = sum(r.games_played for r in rows)
    kills = sum(r.kills * r.games_played for r in rows)
    deaths = sum(r.deaths * r.games_played for r in rows)
    assists = sum(r.assists * r.games_played for r in rows)
    games_won = sum(r.gameWon for r in rows)
    kda = round1((kills + assists) / (deaths if deaths != 0 else 1))
    win_rate = round1(0 if game_played == 0 else games_won / game_played * 100)
    return int(game_played), kda, win_rate

def buildTitlesByRegion(titles):
    titles = sorted(titles, key=lambda t: (t.tournament_year, t.tournament_name or ""))
    by_region = {}
    for title in titles:
        leagues = by_region.setdefault(title.region, {})
        tournaments = leagues.setdefault(title.league_short, [])
        name = title.tournament_name or ""
        if name not in tournaments:
            tournaments.append(name)

    result = {}
    # International first, then the other regions alphabetically.
    for region in sorted(by_region, key=lambda r: (r != "International", r)):
        leagues = {league: LeagueTitlesDto(len(names), names) for league, names in by_region[region].items()}
        result[region] = RegionTitlesDto(sum(l.title for l in leagues.values()), leagues)
    return result

def getPlayersStats(regions, names=None):
    players = getPlayersByRegions(regions)
    if names is not None:
        wanted = {str(n).strip().lower() for n in names}
        players = players[players["IngameName"].fillna("").str.strip().str.lower().isin(wanted)]
    if players.empty:
        return []

    player_ids = players["Id"].tolist()
    # Regions each player has stats in, so a front-only app can filter by region.
    regions_by_player = {}
    for region in regions:
        for pid in getPlayersByRegions([region])["Id"]:
            regions_by_player.setdefault(pid, []).append(region)
    stats_by_player = {}
    for row in getPlayerStats(player_ids, regions):
        stats_by_player.setdefault(row.player_id, []).append(row)
    titles_by_player = {}
    for title in getPlayerTitles(player_ids, regions):
        titles_by_player.setdefault(title.player_id, []).append(title)
    roles_by_player = getPlayerRoles(player_ids)
    leagues_by_player = getLeaguesByPlayer(player_ids)

    result = []
    for player in players.itertuples(index=False):
        rows = stats_by_player.get(player.Id, [])
        game_played, kda, win_rate = summarize(rows)
        # Only keep players with recorded games in the selected regions' tournaments.
        if game_played <= 0:
            continue

        titles = buildTitlesByRegion(titles_by_player.get(player.Id, []))

        by_tournament = {}
        for row in rows:
            by_tournament.setdefault((row.tournament_id, row.tournament_name), []).append(row)
        tournaments = []
        for (_, tournament_name), group in sorted(by_tournament.items(), key=lambda kv: kv[0][1] or ""):
            t_played, t_kda, t_win_rate = summarize(group)
            tournaments.append(TournamentStatsDto(
                name=tournament_name,
                game_played=t_played,
                win_rate=t_win_rate,
                kda=t_kda,
                player_stats=[
                    PlayerTournamentChampionStatsDto(
                        champion=r.champion_name,
                        game_played=int(r.games_played),
                        player_win=r.gameWon,
                        kill=r.kills,
                        death=r.deaths,
                        assist=r.assists,
                        gold=r.gold,
                        cs=r.cs,
                    )
                    for r in sorted(group, key=lambda r: r.champion_name)
                ],
            ))

        nationality = player.Nationality
        result.append(PlayerStatsDto(
            player=player.IngameName,
            image_url=getImageUrl(player.Id),
            role=roles_by_player.get(player.Id, ""),
            nationality=nationality if isinstance(nationality, str) and nationality.strip() else None,
            age=calculateAge(player.Birthdate),
            game_played=game_played,
            kda=kda,
            win_rate=win_rate,
            title=sum(t.title for t in titles.values()),
            titles=titles,
            tournament=tournaments,
            regions=sorted(regions_by_player.get(player.Id, [])),
            leagues_played=leagues_by_player.get(player.Id, []),
        ))
    return result

def getStatsForPlayers(names):
    """Stats (same shape as players_stats.json entries) for the given in-game names, case-insensitive."""
    regions = league_df["Region"].dropna().unique().tolist()
    return [p.to_dict() for p in getPlayersStats(regions, names)]

def main():
    global player_stats_df, primary_player_ids
    parser = argparse.ArgumentParser(description="Generate the player stats JSON used by the frontend.")
    parser.add_argument(
        "--scope",
        choices=("worlds", "all"),
        default="all",
        help="worlds: stats from output/player_champ_stats_worlds.csv -> players_stats_worlds.json; "
             "all: stats from output/player_champ_stats.csv -> players_stats.json (default)",
    )
    args = parser.parse_args()
    if args.scope == "worlds":
        player_stats_df = pd.read_csv("output/player_champ_stats_worlds.csv")
        primary_player_ids = getPrimaryPlayerIds()
        output_path = "esport-frontend/public/players_stats_worlds.json"
    else:
        output_path = "esport-frontend/public/players_stats.json"

    regions = league_df["Region"].dropna().unique().tolist()
    output = [p.to_dict() for p in getPlayersStats(regions)]
    # Compact output: the frontend downloads this file in the browser.
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(len(output), "players")

if __name__ == "__main__":
    main()