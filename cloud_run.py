"""HTTP transport: process Telegram updates within each Cloud Run request."""

import asyncio
import hmac
import logging
import os
import re
from collections import OrderedDict

from aiohttp import web
from telegram import Update


def create_web_app(bot, application=None):
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
        raise ValueError("TELEGRAM_WEBHOOK_SECRET must contain 32–256 letters, digits, underscores or hyphens")
    if not bot.WHITELISTED_CHAT_IDS:
        raise ValueError("WHITELISTED_CHAT_IDS must be configured in webhook mode")
    telegram = application if application is not None else bot.build_application(webhook=True)
    lock = asyncio.Lock()
    completed = OrderedDict()
    failed = False

    async def on_error(update, context):
        nonlocal failed
        failed = True
        # Exception messages from HTTP clients can contain token-bearing URLs.
        logging.error("Telegram command failed (%s)", type(context.error).__name__)

    telegram.add_error_handler(on_error)

    async def startup(app):
        try:
            bot.PLAYERS_LIST = await asyncio.to_thread(bot.load_roster)
            await telegram.initialize()
        except Exception as error:
            raise RuntimeError(f"Webhook initialization failed ({type(error).__name__}); check roster, token and Google identity settings") from None
        print(f"[BOT] Webhook ready; {len(bot.PLAYERS_LIST)} players loaded.")

    async def cleanup(app):
        await telegram.shutdown()

    async def health(request):
        return web.json_response({"status": "ok"})

    async def webhook(request):
        nonlocal failed
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(provided.encode(), secret.encode()):
            raise web.HTTPForbidden()
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or type(payload.get("update_id")) is not int:
                raise ValueError("Invalid update")
            update = Update.de_json(payload, telegram.bot)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise web.HTTPBadRequest(text="Invalid Telegram update") from None
        if not update.effective_chat or not bot.is_whitelisted(update.effective_chat.id):
            return web.Response(text="OK")
        async with lock:
            if update.update_id in completed:
                return web.Response(text="OK")
            failed = False
            # Await handlers directly: queued background work can stop after
            # Cloud Run returns an HTTP response under request-based billing.
            try:
                await telegram.process_update(update)
            except Exception as error:
                logging.error("Update processing failed (%s)", type(error).__name__)
                raise web.HTTPServiceUnavailable(text="Command failed; retry later") from None
            if failed:
                raise web.HTTPServiceUnavailable(text="Command failed; retry later")
            completed[update.update_id] = None
            if len(completed) > 1024:
                completed.popitem(last=False)
        return web.Response(text="OK")

    app = web.Application(client_max_size=1024 * 1024)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    app.router.add_get("/healthz", health)
    app.router.add_post("/telegram", webhook)
    return app


def run_webhook(bot):
    # httpx INFO logs include Telegram API URLs containing the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    web.run_app(create_web_app(bot), host="0.0.0.0",
                port=int(os.getenv("PORT", "8080")), access_log=None)
