# fetch-data

Scripts that download LoL esports data from [Leaguepedia](https://lol.fandom.com) (via `mwrogue`) and write CSVs to `output/`. `generate_data.py` then turns those CSVs into the JSON files used by the website.

## Setup

Run everything from the **repository root**, because the default paths are relative (`output/...`).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install mwrogue pandas
```

Credentials: the scripts log in with `AuthCredentials(user_file="me")`. The first run prompts for your Leaguepedia bot username and password and saves them under `~/.config/mwcleric`. They can instead come from the environment variables `WIKI_USERNAME_ME` and `WIKI_PASSWORD_ME`. Never commit credentials.
For more information about Leaguepedia API authentication and credentials, see the [Leaguepedia API documentation](https://lol.fandom.com/wiki/Help:Leaguepedia_API).
## Run order

Each script reads the CSVs produced by the earlier ones:

```powershell
python fetch-data\fetchLeaguepedia.py        # leagues.csv
python fetch-data\fetchChampion.py           # champions.csv
python fetch-data\fetchTournament.py         # tournaments.csv
python fetch-data\fetchTeam.py               # teams.csv
python fetch-data\fetchPlayer.py             # players.csv
python fetch-data\fetchRoster.py             # roster.csv
python fetch-data\fetchTeamResult.py         # team_results.csv
python fetch-data\fetchPlayerChampStats.py   # player_champ_stats.csv
python fetch-data\fetchPlayerChampStats.py --worlds-only   # worlds stats (see --help)
python fetch-data\fetchPlayerImages.py       # images.csv (player photo URLs)
```

Then generate the website data:

```powershell
python generate_data.py            # esport-frontend/public/players_stats.json
python generate_data.py --help     # option for the Worlds-only file
```

Every script supports `--help` for options such as `--out`, `--tournament-level` and `--is-qualifier`.

## Files

- `data_helpers.py`: shared loaders and stable ID generation.
- `exchange_rates.json`: currency rates used to convert prize money to USD.
- `data_quality_and_pipeline.ipynb`: data checks and a pipeline diagram.

## Notes

- Requests are rate-limited with sleeps, so full runs can take a while.
- Commit the updated JSON in `esport-frontend/public/` so the site redeploys with the new data.
