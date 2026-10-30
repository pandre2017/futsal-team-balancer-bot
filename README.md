# Futsal Team Balancer Bot

A Telegram bot that splits a list of players into two teams of roughly equal
strength, based on a per-player skill score.

Give it the players who showed up, and it answers with a 5v5 (or any even-sized)
split plus the score gap between the two sides.

```
/balance
1 John Doe
2 - Jane Smith
3 Alice Johnson
...
```

```
Team A score=18.33
John Doe  4.50
...

Team B score=18.25
George Miller  5.00
...

delta=0.08
```

## Commands

| Command | What it does |
| --- | --- |
| `/start` | Greeting, confirms the bot is alive |
| `/help` | Lists the available commands |
| `/balance` | Splits the given players into two balanced teams |
| `/format` | Turns two team lines into a TSV file for pasting into a spreadsheet |

`/balance` accepts comma-separated names on one line, or one name per line, with
or without numbering (`1 John`, `2 - Jane`, `3. Alice`). Players can be given by
alias or by name; accents, casing and invisible characters pasted from chat apps
are ignored.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install python-telegram-bot python-dotenv
```

Configure the bot:

```bash
cp .env.example .env     # add your BotFather token and allowed chat IDs
cp players.example.csv players.csv   # then edit in your own players
```

Run it:

```bash
python team-balancer-bot.py
```

## The roster

Players live in `players.csv`, next to the script:

```csv
Nome,Alias,Aliases,Grupo,Score Avaliador 1,Score Avaliador 2,Score Avaliador 3,Score Avaliador 4,Avg Score
John Doe,jdoe,"jdoe, john",Monday Hall,4.50,,,4.50,4.50
```

- **Nome** — display name; players can be identified by their name in `/balance`.
- **Alias / Aliases** — optional handles for each player. Separate multiple aliases
  with commas and quote the CSV cell, as above. Either or both columns can be used;
  aliases must be unique across players. Matching ignores case and accents.
- **Avg Score** — the score used for balancing. The script reads this value;
  it does not calculate an average from individual ratings.
- **Grupo / Score Avaliador …** — optional information ignored by the bot.

The older `alias,name,group,score` format is also supported. Columns can appear
in any order; `Avg Score` takes priority over `score` if both exist.

The file is validated on startup: a bad score, a duplicate alias or a missing
column stops the bot with the offending line number rather than failing later
mid-command.

`players.csv` is gitignored, because a real roster holds other people's names
and your opinion of their ability. `players.example.csv` is the fictional
stand-in that ships with the repo.

## How balancing works

`find_most_balanced_teams` shuffles the players 100 times, splits each shuffle
down the middle and keeps whichever split has the smallest score difference.
It is a random sample rather than an exhaustive search, so it is fast and good
enough in practice, but it is not guaranteed to find the optimal split.

## Access control

The bot answers only in chats listed in `WHITELISTED_CHAT_IDS`; everything else
is silently ignored. Send `/start` from the chat you want to allow and read the
chat ID from the console output.
