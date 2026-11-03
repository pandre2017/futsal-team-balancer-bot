"""Offline checks for the webhook boundary and Google authentication selection."""

import asyncio
import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from telegram import Bot

from cloud_run import create_web_app


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "a" * 64})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.telegram = SimpleNamespace(
            bot=Bot("123:test"), initialize=AsyncMock(), shutdown=AsyncMock(),
            process_update=AsyncMock(), add_error_handler=Mock())
        self.bot = SimpleNamespace(
            WHITELISTED_CHAT_IDS=[42], load_roster=Mock(return_value=["player"]),
            is_whitelisted=lambda chat_id: chat_id == 42)
        self.app = create_web_app(self.bot, self.telegram)
        self.handler = next(route.handler for route in self.app.router.routes()
                            if route.method == "POST")

    def request(self, secret="a" * 64, chat_id=42, update_id=1):
        request = make_mocked_request("POST", "/telegram", headers={
            "X-Telegram-Bot-Api-Secret-Token": secret})
        request.json = AsyncMock(return_value={
            "update_id": update_id,
            "message": {"message_id": 1, "date": 0,
                        "chat": {"id": chat_id, "type": "private"}, "text": "/help"}})
        return request

    async def test_rejects_wrong_secret_before_reading_body(self):
        request = self.request(secret="wrong")
        with self.assertRaises(web.HTTPForbidden):
            await self.handler(request)
        request.json.assert_not_awaited()
        self.telegram.process_update.assert_not_awaited()

    async def test_rejects_malformed_update(self):
        request = self.request()
        request.json.return_value = []
        with self.assertRaises(web.HTTPBadRequest):
            await self.handler(request)

    async def test_ignores_unapproved_chat(self):
        self.assertEqual((await self.handler(self.request(chat_id=99))).status, 200)
        self.telegram.process_update.assert_not_awaited()

    async def test_awaits_processing_and_suppresses_successful_duplicate(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def process(update):
            entered.set()
            await release.wait()

        self.telegram.process_update.side_effect = process
        task = asyncio.create_task(self.handler(self.request()))
        await entered.wait()
        self.assertFalse(task.done())
        release.set()
        self.assertEqual((await task).status, 200)
        self.assertEqual((await self.handler(self.request())).status, 200)
        self.telegram.process_update.assert_awaited_once()

    async def test_failed_command_is_retryable(self):
        error_handler = self.telegram.add_error_handler.call_args.args[0]

        async def fail(update):
            await error_handler(update, SimpleNamespace(error=RuntimeError("private details")))

        self.telegram.process_update.side_effect = fail
        with self.assertLogs(level="ERROR") as logs:
            with self.assertRaises(web.HTTPServiceUnavailable):
                await self.handler(self.request())
        self.assertNotIn("private details", " ".join(logs.output))
        self.telegram.process_update.side_effect = None
        self.assertEqual((await self.handler(self.request())).status, 200)
        self.assertEqual(self.telegram.process_update.await_count, 2)

    async def test_startup_cleanup_and_health(self):
        await self.app.on_startup[-1](self.app)
        self.telegram.initialize.assert_awaited_once()
        self.assertEqual(self.bot.PLAYERS_LIST, ["player"])
        route = next(r for r in self.app.router.routes() if r.method == "GET")
        self.assertEqual((await route.handler(make_mocked_request("GET", "/healthz"))).status, 200)
        await self.app.on_cleanup[-1](self.app)
        self.telegram.shutdown.assert_awaited_once()


class GoogleAuthTests(unittest.TestCase):
    def test_adc_and_existing_local_key(self):
        spec = importlib.util.spec_from_file_location("balancer", Path(__file__).with_name("team-balancer-bot.py"))
        bot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot)
        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "", "GOOGLE_SHEETS_SPREADSHEET_ID": "example", "GOOGLE_SHEETS_RANGE": "Players!A:Z"}), \
                patch("google.auth.default", return_value=(Mock(), "project")) as adc, \
                patch("google.oauth2.service_account.Credentials.from_service_account_file", return_value=Mock()) as local, \
                patch("googleapiclient.discovery.build") as build:
            service = build.return_value.__enter__.return_value
            service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {"values": [["Name", "Aliases", "Avg Score"], ["John", "john", 4.5]]}
            self.assertEqual(bot.read_google_sheet()["values"][1][2], 4.5)
            adc.assert_called_once()
            local.assert_not_called()
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ".secrets/example.json"
            bot.read_google_sheet()
            local.assert_called_once()
            request = service.spreadsheets.return_value.values.return_value.get.call_args.kwargs
            self.assertEqual(request["valueRenderOption"], "UNFORMATTED_VALUE")


if __name__ == "__main__":
    unittest.main()
