from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import csv
import io
import os
import re
import unicodedata
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WHITELISTED_CHAT_IDS = os.getenv("WHITELISTED_CHAT_IDS", "")
WHITELISTED_CHAT_IDS = [int(x.strip()) for x in WHITELISTED_CHAT_IDS.split(",") if x.strip()]


# classes

class Player:
    def __init__(self, alias: str, name: str, score: float, group: str = ""):
        self.alias = alias
        self.name = name
        self.score = score
        self.group = group

    def __str__(self):
        return f"Player(alias='{self.alias}', name='{self.name}', score={self.score}, group='{self.group}')"


class Team:
    def __init__(self, name: str, score: float, players: list):
        self.name = name
        self.score = score
        self.players = players  # A list of Player instances

    def __str__(self):
        return f"Team(name='{self.name}', score={self.score}, players={[p.alias for p in self.players]})"


# players

PLAYERS_FILE = Path(__file__).resolve().parent / "players.csv"


def load_players(path: Path = PLAYERS_FILE) -> list:
    """Load the player roster from a CSV file sitting next to this script.

    Expected columns: alias, name, group, score
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Player roster not found: {path}\n"
            f"Copy players.example.csv to {path.name} and fill in your own players."
        )

    players = []
    seen = {}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = {"alias", "name", "score"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing column(s): {', '.join(sorted(missing))}")

        for lineno, row in enumerate(reader, start=2):
            alias = (row.get("alias") or "").strip()
            name = (row.get("name") or "").strip()
            raw_score = (row.get("score") or "").strip()
            if not alias and not name and not raw_score:
                continue  # tolerate blank spacer rows
            if not alias:
                raise ValueError(f"{path.name} line {lineno}: missing alias")
            if not name:
                raise ValueError(f"{path.name} line {lineno}: player '{alias}' is missing a name")
            try:
                score = float(raw_score.replace(",", "."))
            except ValueError:
                raise ValueError(
                    f"{path.name} line {lineno}: player '{alias}' has an invalid score {raw_score!r}"
                ) from None
            if alias.lower() in seen:
                raise ValueError(
                    f"{path.name} line {lineno}: duplicate alias '{alias}' "
                    f"(already defined on line {seen[alias.lower()]})"
                )
            seen[alias.lower()] = lineno
            players.append(Player(alias=alias, name=name, score=score,
                                  group=(row.get("group") or "").strip()))

    if not players:
        raise ValueError(f"{path.name} contains no players")
    return players


PLAYERS_LIST = load_players()


# methods

def sanitize_string(text: str) -> str:
    """Remove accents, invisible formatting marks, and lowercase for comparison."""
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) not in ('Mn', 'Cf'))
    return text.strip().lower()

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
        print(f"Looking for: {alias}")
        sanitized_input = sanitize_string(alias)
        
        # Try to find by alias
        found_player = next((p for p in PLAYERS_LIST if sanitize_string(p.alias) == sanitized_input), None)
        
        # If not found by alias, try to find by name (substring matching)
        if not found_player:
            found_player = next((p for p in PLAYERS_LIST if sanitized_input in sanitize_string(p.name)), None)
        
        if found_player: 
            print(f"Found: {found_player}")
            players.append(found_player)
        else:
            print(f"player {alias} not found")
            await update.message.reply_text(f"player {alias} not found")
            return

    if len(players) % 2 != 0:
        print(f"an even number of players is required")
        await update.message.reply_text(f"an even number of players is required")
        return
    
    teamA, teamB, delta = find_most_balanced_teams(players)
    print(f"TeamA={teamA}")
    print(f"TeamB={teamB}")
    result = f"{teamA.name} score={teamA.score:.2f}\n"
    for p in teamA.players:
        result += f"{p.name}  {p.score}\n"
    result += f"\n{teamB.name} score={teamB.score:.2f}\n"
    for p in teamB.players:
        result += f"{p.name}  {p.score}\n"
    result += f"\ndelta={delta:.2f}"
    print(result)
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


# main - app and commands
if __name__ == '__main__':
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not WHITELISTED_CHAT_IDS:
        print("[BOT] Warning: WHITELISTED_CHAT_IDS is empty, the bot will ignore every chat.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    print(f"[BOT] Starting. Token loaded ({len(BOT_TOKEN)} chars), "
          f"{len(WHITELISTED_CHAT_IDS)} whitelisted chat(s), {len(PLAYERS_LIST)} players.")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", balance_teams_command))
    app.add_handler(CommandHandler("format", format_command))

    print("[BOT] Handlers registered. Starting polling...")
    app.run_polling()