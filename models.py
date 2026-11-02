# class PlayerStats:
#     def __init__(self,id,role,nationality,age,game_played, kda,winrate,titles,title_count,tournaments):

class playerChampStats:
    def __init__(self, player_id, tournament_id, tournament_name, champion_name,\
                 games_played, gameWon, kills, deaths, assists, gold, cs, kda):
        self.player_id = player_id
        self.tournament_id = tournament_id
        self.tournament_name = tournament_name
        self.champion_name = champion_name
        self.games_played = games_played
        self.gameWon = gameWon
        self.kills = kills
        self.deaths = deaths
        self.assists = assists
        self.gold = gold
        self.cs = cs
        self.kda = kda

class PlayerTitle:
    def __init__(self, player_id, region, league_short, team_name, tournament_name, tournament_year, role):
        self.player_id = player_id
        self.region = region
        self.league_short = league_short
        self.team_name = team_name
        self.tournament_name = tournament_name
        self.tournament_year = tournament_year
        self.role = role

    def __repr__(self):
        return f"PlayerTitle({vars(self)})"


class PlayerTournamentChampionStatsDto:
    def __init__(self, champion, game_played, player_win, kill, death, assist, gold, cs):
        self.champion = champion
        self.game_played = game_played
        self.player_win = player_win
        self.kill = kill
        self.death = death
        self.assist = assist
        self.gold = gold
        self.cs = cs

    def to_dict(self):
        return {
            "champion": self.champion,
            "gamePlayed": self.game_played,
            "playerWin": self.player_win,
            "kill": self.kill,
            "death": self.death,
            "assist": self.assist,
            "gold": self.gold,
            "cs": self.cs,
        }


class TournamentStatsDto:
    def __init__(self, name, game_played, win_rate, kda, player_stats):
        self.name = name
        self.game_played = game_played
        self.win_rate = win_rate
        self.kda = kda
        self.player_stats = player_stats

    def to_dict(self):
        return {
            "name": self.name,
            "gamePlayed": self.game_played,
            "winRate": self.win_rate,
            "kda": self.kda,
            "playerStats": [s.to_dict() for s in self.player_stats],
        }


class LeagueTitlesDto:
    def __init__(self, title, tournaments):
        self.title = title
        self.tournaments = tournaments

    def to_dict(self):
        return {"title": self.title, "tournaments": list(self.tournaments)}


class RegionTitlesDto:
    def __init__(self, title, titles):
        self.title = title
        self.titles = titles

    def to_dict(self):
        return {"title": self.title, "titles": {league: t.to_dict() for league, t in self.titles.items()}}


class PlayerStatsDto:
    def __init__(self, player, role, nationality, age, game_played, kda, win_rate,
                 title, titles, tournament, regions, image_url=None, leagues_played=None):
        self.player = player
        self.leagues_played = leagues_played or []
        self.image_url = image_url
        self.role = role
        self.nationality = nationality
        self.age = age
        self.game_played = game_played
        self.kda = kda
        self.win_rate = win_rate
        self.title = title
        self.titles = titles
        self.tournament = tournament
        self.regions = regions

    # camelCase keys, matching the JSON the ASP.NET API returns.
    def to_dict(self):
        return {
            "player": self.player,
            "imageUrl": self.image_url,
            "role": self.role,
            "nationality": self.nationality,
            "age": self.age,
            "gamePlayed": self.game_played,
            "kda": self.kda,
            "winRate": self.win_rate,
            "title": self.title,
            "titles": {region: t.to_dict() for region, t in self.titles.items()},
            "tournament": [t.to_dict() for t in self.tournament],
            "regions": self.regions,
            "leaguesPlayed": self.leagues_played,
        }