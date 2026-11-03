from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import argparse
import csv
import io
import os
import re
import sys
import unicodedata
from pathlib import Path
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WHITELISTED_CHAT_IDS = os.getenv("WHITELISTED_CHAT_IDS", "")
WHITELISTED_CHAT_IDS = [int(x.strip()) for x in WHITELISTED_CHAT_IDS.split(",") if x.strip()]


def sanitize_string(text: str) -> str:
    """Remove accents, invisible formatting marks, and lowercase for comparison."""
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) not in ('Mn', 'Cf'))
    return text.strip().lower()


# classes

class Player:
    def __init__(self, alias: str, name: str, score: float, aliases=None):
        self.alias = alias
        self.aliases = list(dict.fromkeys([a for a in [alias, *(aliases or [])] if a]))
        self.name = name
        self.score = score

    def __str__(self):
        return f"Player(alias='{self.alias}', name='{self.name}', score={self.score})"


class Team:
    def __init__(self, name: str, score: float, players: list):
        self.name = name
        self.score = score
        self.players = players  # A list of Player instances

    def __str__(self):
        return f"Team(name='{self.name}', score={self.score}, players={[p.alias for p in self.players]})"


# players

PLAYERS_FILE = Path(os.getenv("PLAYERS_FILE", "players.csv")).expanduser()
if not PLAYERS_FILE.is_absolute():
    PLAYERS_FILE = Path(__file__).resolve().parent / PLAYERS_FILE


def load_players(path: Path = PLAYERS_FILE) -> list:
    """Read the local CSV and validate its roster."""
    if not path.exists():
        raise FileNotFoundError(f"Player roster not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as file:
        return parse_players(csv.reader(file), path.name)


def parse_players(rows, source: str) -> list:
    """Parse CSV or Sheets rows with the same header, alias, and score rules."""
    players = []
    seen = {}
    reader = iter(rows)
    headers = next(reader, None)
    if not headers or len(headers) < 3:
        raise ValueError(f"{source} must have name, alias, and average-score columns")

    normalized_headers = [header.strip().casefold() for header in headers]

    def column_index(*labels):
        for label in labels:
            if label in normalized_headers:
                return normalized_headers.index(label)
        raise ValueError(
            f"{source}: missing required column ({' or '.join(labels)})"
        )

    name_index = column_index("nome", "name")
    alias_indices = [i for i, header in enumerate(normalized_headers)
                     if header in {"alias", "aliases"}]
    if not alias_indices:
        raise ValueError(f"{source}: missing required column (alias or aliases)")
    score_index = column_index("avg score", "average score", "score")

    for lineno, row in enumerate(reader, start=2):
        row = ["" if value is None else str(value) for value in row]
        if not row or all(not value.strip() for value in row):
            continue

        name = (row[name_index] if len(row) > name_index else "").strip()
        aliases = {}
        for index in alias_indices:
            for value in (row[index] if len(row) > index else "").split(","):
                value = value.strip()
                key = sanitize_string(value)
                if key:
                    aliases.setdefault(key, value)
        alias = next(iter(aliases.values()), "")
        raw_score = (row[score_index] if len(row) > score_index else "").strip()
        if not alias and not name and not raw_score:
            continue
        if not name:
            raise ValueError(f"{source} line {lineno}: player '{alias}' is missing a name")
        try:
            score = float(raw_score.replace(",", "."))
        except ValueError:
            raise ValueError(
                f"{source} line {lineno}: player '{name}' has an invalid average score {raw_score!r}"
            ) from None
        for key, value in aliases.items():
            if key in seen:
                raise ValueError(
                    f"{source} line {lineno}: duplicate alias '{value}' "
                    f"(already defined on line {seen[key]})"
                )
            seen[key] = lineno
        players.append(Player(alias=alias, name=name, score=score,
                              aliases=list(aliases.values())))
    if not players:
        raise ValueError(f"{source} contains no players")
    return players


PLAYERS_LIST = []


def read_google_sheet():
    """Read the configured spreadsheet with read-only service account access."""
    settings = {
        key: os.getenv(key, "").strip()
        for key in ("GOOGLE_SHEETS_SPREADSHEET_ID", "GOOGLE_SHEETS_RANGE")
    }
    missing = [key for key, value in settings.items() if not value]
    if missing:
        raise ValueError(f"Missing configuration in .env: {', '.join(missing)}")

    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        raise ValueError("Google libraries are missing. Run: python -m pip install -r requirements.txt") from None

    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if key_path:
            credentials_path = Path(key_path).expanduser()
            if not credentials_path.is_absolute():
                credentials_path = PROJECT_DIR / credentials_path
            credentials = Credentials.from_service_account_file(str(credentials_path), scopes=scopes)
        else:
            credentials, _ = google.auth.default(scopes=scopes)
    except (ValueError, OSError, DefaultCredentialsError):
        raise ValueError("Google authentication failed. Configure a local JSON key or attach a service account in Cloud Run.") from None

    try:
        with build("sheets", "v4", credentials=credentials, cache_discovery=False) as service:
            result = service.spreadsheets().values().get(
                spreadsheetId=settings["GOOGLE_SHEETS_SPREADSHEET_ID"],
                range=settings["GOOGLE_SHEETS_RANGE"],
                majorDimension="ROWS",
                valueRenderOption="UNFORMATTED_VALUE",
            ).execute(num_retries=2)
    except HttpError as error:
        hints = {
            400: "Check GOOGLE_SHEETS_RANGE; it must include the exact tab name.",
            401: "Check the service account credentials and whether the key is active.",
            403: "Enable the Sheets API and share the spreadsheet with the service account as Viewer.",
            404: "Check the spreadsheet ID and share the spreadsheet with the service account.",
        }
        raise ValueError(
            f"Google Sheets request failed (HTTP {error.resp.status}). "
            + hints.get(error.resp.status, "Try again or check the Google API status.")
        ) from None
    except Exception as error:
        raise ValueError(
            f"Google Sheets connection failed ({type(error).__name__}). "
            "Check your network and service account credentials."
        ) from None

    return result


def print_google_sheet():
    """Print spreadsheet contents without starting Telegram."""
    result = read_google_sheet()
    rows = result.get("values", [])
    print(f"[SHEETS] Read {len(rows)} rows, including the header, from {result.get('range', '')}.")
    if not rows:
        print("[SHEETS] The selected range is empty.")
        return
    writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    writer.writerows(rows)


def load_roster():
    """Select the roster source at startup; do not silently fall back."""
    source = os.getenv("PLAYERS_SOURCE", "csv").strip().lower()
    if source == "csv":
        return load_players()
    if source == "google_sheets":
        result = read_google_sheet()
        return parse_players(result.get("values", []), "Google Sheets")
    raise ValueError("PLAYERS_SOURCE must be csv or google_sheets")


# methods

def find_player(identifier: str, players=None):
    """Match a full name or any alias before falling back to a partial name."""
    players = PLAYERS_LIST if players is None else players
    identifier = sanitize_string(identifier)
    if not identifier:
        return None
    for player in players:
        if identifier == sanitize_string(player.name) or any(
            identifier == sanitize_string(alias) for alias in player.aliases
        ):
            return player
    return next((p for p in players if identifier in sanitize_string(p.name)), None)

def is_whitelisted(chat_id):
    return chat_id in WHITELISTED_CHAT_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    print(f"[START] Chat ID: {chat_id}")
    if not is_whitelisted(chat_id):
        print(f"[DENIED] Message from non-whitelisted chat: {chat_id}")
        return  # Do nothing if not whitelisted
    await update.message.reply_text("Hello! I'm the Futsal Team Balancer bot.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_whitelisted(chat_id):
        print(f"[DENIED] Message from non-whitelisted chat: {chat_id}")
        return  # Do nothing if not whitelisted
    
    await update.message.reply_text("These are the available commands: /start /help /balance /format")


def parse_format_input_line(line: str):
    if ":" not in line:
        return None

    team_name_part, rest = line.split(":", 1)
    team_name_part = team_name_part.strip()
    rest = rest.strip()

    if not team_name_part:
        return None

    score = None
    team_name = team_name_part
    players_part = rest

    # Try score after colon: "Team A: 13 player1, player2"
    # Also accepts optional "score" label like "Team A: score=13 player1, player2"
    score_after = re.match(r"^(?:score\s*[:=\-]?\s*)?(\d+(?:\.\d+)?)\s+(.*)$", rest, flags=re.IGNORECASE)
    if score_after:
        score = score_after.group(1)
        players_part = score_after.group(2).strip()
    else:
        # Try score before colon: "Team A 13: player1, player2"
        score_before = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?)\s*$", team_name_part)
        if score_before:
            team_name = score_before.group(1).strip()
            score = score_before.group(2)
            players_part = rest

    if score is None:
        return None

    players_part = re.sub(r"^[\-\|]+\s*", "", players_part)
    players = [p.strip() for p in re.split(r"[;,]", players_part) if p.strip()]

    return {
        "team_name": team_name,
        "score": score,
        "players": players,
    }


def build_excel_output(team_a_data, team_b_data):
    rows = []
    rows.append(
        "\t".join(
            [
                team_a_data["team_name"],
                team_a_data["score"],
                team_b_data["score"],
                team_b_data["team_name"],
            ]
        )
    )

    max_players = max(len(team_a_data["players"]), len(team_b_data["players"]))
    for i in range(max_players):
        col_1 = team_a_data["players"][i] if i < len(team_a_data["players"]) else ""
        col_4 = team_b_data["players"][i] if i < len(team_b_data["players"]) else ""
        rows.append(f"{col_1}\t\t\t{col_4}")

    return "\n".join(rows)


async def format_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_whitelisted(chat_id):
        print(f"[DENIED] Message from non-whitelisted chat: {chat_id}")
        return  # Do nothing if not whitelisted

    message_text = update.message.text or ""
    if message_text.startswith("/format"):
        message_text = message_text[7:].strip()

    lines = [line.strip() for line in message_text.split("\n") if line.strip()]
    if len(lines) < 2:
        await update.message.reply_text(
            "Please send two lines in this format:\n"
            "/format\n"
            "Team A: 10 Alice, Bob; Carol\n"
            "Team B: 8 Dave; Eve, Frank"
        )
        return

    team_a_data = parse_format_input_line(lines[0])
    team_b_data = parse_format_input_line(lines[1])

    if not team_a_data or not team_b_data:
        await update.message.reply_text(
            "Invalid input format. Use:\n"
            "Team A: score player1, player2; player3\n"
            "Team B: score player4; player5, player6"
        )
        return

    tsv_content = build_excel_output(team_a_data, team_b_data)
    tsv_file = io.BytesIO(tsv_content.encode("utf-8"))
    await update.message.reply_document(document=tsv_file, filename="teams.tsv")

async def balance_teams_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_whitelisted(chat_id):
        print(f"[DENIED] Message from non-whitelisted chat: {chat_id}")
        return  # Do nothing if not whitelisted
    
    # Get the full message text to support multi-line input
    message_text = update.message.text
    
    # Remove the "/balance" command from the message
    if message_text.startswith("/balance"):
        message_text = message_text[8:].strip()
    
    if not message_text:
        await update.message.reply_text("Please provide player names. Examples:\n/balance john,bill,joe\nor\n/balance\n1 John\n2-Bill\n3 - Joe")
        return

    # Extract player names from input
    # Handle multiple formats:
    # 1. Comma-separated: "john, bill, joe"
    # 2. Multi-line: "1 John\n2 Bill"
    # 3. Multi-line with various separators: "1 John\n2-Bill\n3 - Joe"
    
    items = []
    
    # Split by both newlines and commas
    for line in message_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Then by commas within each line
        for comma_item in line.split(','):
            comma_item = comma_item.strip()
            if not comma_item:
                continue
            
            # Remove optional numbering prefix (e.g., "1 ", "2-", "3 - ")
            cleaned = re.sub(r'^\d+\s*[-\s]*', '', comma_item).strip()
            if cleaned:
                items.append(cleaned)
    
    if not items:
        await update.message.reply_text("No valid items found.")
        return

    players = []
    for alias in items:
        found_player = find_player(alias)
        
        if found_player: 
            players.append(found_player)
        else:
            await update.message.reply_text(f"player {alias} not found")
            return

    if len(players) % 2 != 0:
        print(f"an even number of players is required")
        await update.message.reply_text(f"an even number of players is required")
        return
    
    teamA, teamB, delta = find_most_balanced_teams(players)
    result = f"{teamA.name} score={teamA.score:.2f}\n"
    for p in teamA.players:
        result += f"{p.name}  {p.score:.2f}\n"
    result += f"\n{teamB.name} score={teamB.score:.2f}\n"
    for p in teamB.players:
        result += f"{p.name}  {p.score:.2f}\n"
    result += f"\ndelta={delta:.2f}"
    await update.message.reply_text(result)

    result_no_scores = f"{teamA.name}\n"
    for p in teamA.players:
        result_no_scores += f"{p.name}\n"
    result_no_scores += f"\n{teamB.name}\n"
    for p in teamB.players:
        result_no_scores += f"{p.name}\n"
    await update.message.reply_text(result_no_scores)
    return

import random

def find_most_balanced_teams(players):
    smallest_delta_team_a = None
    smallest_delta_team_b = None
    smallest_delta = float('inf')

    for _ in range(100):
        shuffled_players = players[:]
        random.shuffle(shuffled_players)

        mid = len(shuffled_players) // 2
        team_a_players = sorted(shuffled_players[:mid], key=lambda p: p.score, reverse=True)
        team_b_players = sorted(shuffled_players[mid:], key=lambda p: p.score, reverse=True)

        team_a_score = sum(player.score for player in team_a_players)
        team_b_score = sum(player.score for player in team_b_players)

        team_a = Team("Team A", team_a_score, team_a_players)
        team_b = Team("Team B", team_b_score, team_b_players)

        delta = abs(team_a.score - team_b.score)

        if delta < smallest_delta:
            smallest_delta = delta
            smallest_delta_team_a = team_a
            smallest_delta_team_b = team_b

    if smallest_delta_team_a is None or smallest_delta_team_b is None:
        raise ValueError("Could not find valid teams.")

    return smallest_delta_team_a, smallest_delta_team_b, smallest_delta


def build_application(webhook=False):
    builder = ApplicationBuilder().token(BOT_TOKEN)
    if webhook:
        builder = builder.updater(None)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", balance_teams_command))
    app.add_handler(CommandHandler("format", format_command))
    return app


def main():
    global PLAYERS_LIST
    parser = argparse.ArgumentParser(description="Futsal team balancer bot")
    parser.add_argument("--print-google-sheet", action="store_true",
                        help="Read and print the configured Google Sheets range, then exit")
    args = parser.parse_args()
    if args.print_google_sheet:
        print_google_sheet()
        return
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    mode = os.getenv("BOT_MODE", "polling").strip().lower()
    if mode not in {"polling", "webhook"}:
        raise ValueError("BOT_MODE must be polling or webhook")
    if mode == "webhook":
        from cloud_run import run_webhook
        run_webhook(sys.modules[__name__])
        return
    PLAYERS_LIST = load_roster()
    print(f"[BOT] Loaded {len(PLAYERS_LIST)} players. Starting polling.")
    build_application().run_polling()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[BOT] {error}") from None
