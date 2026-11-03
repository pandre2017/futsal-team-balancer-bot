"""Explicitly register, inspect, or delete a Telegram webhook."""

import argparse
import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from telegram import Bot


async def manage(args):
    load_dotenv(Path(__file__).resolve().parent / ".env")
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise ValueError("BOT_TOKEN is required")
    async with Bot(token) as bot:
        if args.action == "set":
            url = args.url or os.getenv("WEBHOOK_URL", "")
            parts = urlsplit(url)
            if (parts.scheme != "https" or not parts.hostname or parts.username
                    or parts.query or parts.fragment or parts.path != "/telegram"):
                raise ValueError("Provide a public HTTPS endpoint URL, ending in /telegram")
            secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
            import re
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
                raise ValueError("Configure TELEGRAM_WEBHOOK_SECRET (32–256 letters, digits, underscores or hyphens)")
            await bot.set_webhook(url=url, secret_token=secret,
                                  allowed_updates=["message"], max_connections=1,
                                  drop_pending_updates=False)
            print("Webhook registered; pending updates preserved.")
        elif args.action == "delete":
            await bot.delete_webhook(drop_pending_updates=False)
            print("Webhook removed; polling can now be started.")
        else:
            info = await bot.get_webhook_info()
            print("Webhook configured:", bool(info.url))
            print("Pending updates:", info.pending_update_count)
            print("Delivery error recorded:", bool(info.last_error_date))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["set", "status", "delete"])
    parser.add_argument("--url", help="Full public HTTPS webhook URL")
    args = parser.parse_args()
    try:
        asyncio.run(manage(args))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        raise SystemExit(f"Telegram operation failed ({type(error).__name__}); check credentials and connectivity.") from None
