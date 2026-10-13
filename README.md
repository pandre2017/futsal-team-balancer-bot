# Futsal Team Balancer Bot

A Telegram bot that splits a list of players into two teams of roughly equal
strength, based on a per-player skill score.

Give it the players who showed up, and it answers with a 5v5 (or any even-sized)
split plus the score gap between the two sides.

```
/balance
1 Ana Costa
2 - Bruno Silva
3 Carla Mendes
...
```

```
Team A score=18.33
Ana Costa  4.50
...

Team B score=18.25
Gui Pinto  5.00
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
or without numbering (`1 Ana`, `2 - Bruno`, `3. Carla`). Players can be given by
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
alias,name,group,score
acosta,Ana Costa,Monday Hall,4.50
```

- **alias** — short handle, unique, what you type in `/balance`
- **name** — display name shown in the output
- **group** — which regular game they belong to (free text, optional)
- **score** — skill rating; the balancer only cares about relative values

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
