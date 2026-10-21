import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from gvw_bot.heartbeat import HeartbeatManager
from gvw_bot.bot_handler import REPLY_KEYBOARD, TelegramBotHandler
from gvw_bot.scheduler import GVWScheduler, ScheduledJob

class TestBotAndScheduler(unittest.TestCase):

    def test_startup_modules_import(self):
        import gvw_bot.bot_handler
        import src.persistence.sqlite_repository

    def test_bot_sends_persistent_keyboard(self):
        bot = TelegramBotHandler(token="test-token")
        response = Mock(status_code=200)

        with patch("gvw_bot.bot_handler.requests.post", return_value=response) as post:
            self.assertTrue(bot.send_message(123, "Menu"))

        self.assertEqual(post.call_args.kwargs["json"]["reply_markup"], REPLY_KEYBOARD)

    def test_polling_routes_keyboard_button_text(self):
        bot = TelegramBotHandler(token="test-token")
        bot.handle_command = Mock()
        response = Mock(
            status_code=200,
            json=lambda: {
                "result": [{
                    "update_id": 1,
                    "message": {
                        "text": "📊 選股報告 / Top 10",
                        "chat": {"id": 123},
                    },
                }]
            },
        )

        with patch("gvw_bot.bot_handler.requests.get", return_value=response):
            bot.poll_once()

        bot.handle_command.assert_called_once_with(
            123, "📊 選股報告 / Top 10", "📊 選股報告 / Top 10"
        )

    def test_ai_analysis_keyboard_prompts_for_ticker_then_analyzes_it(self):
        bot = TelegramBotHandler(token="test-token")
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.analyze_stock", return_value="多空分析") as analyze:
            bot.handle_command(123, "🤖 AI 個股分析", "🤖 AI 個股分析")

            analyze.assert_not_called()

        self.assertIn(123, bot.pending_analysis_chats)
        self.assertIn("請輸入 4 至 6 位數", bot.send_message.call_args.args[1])

        response = Mock(
            status_code=200,
            json=lambda: {
                "result": [{
                    "update_id": 1,
                    "message": {"text": "2330", "chat": {"id": 123}},
                }]
            },
        )
        with (
            patch("gvw_bot.bot_handler.analyze_stock", return_value="多空分析") as analyze,
            patch("gvw_bot.bot_handler.requests.get", return_value=response),
        ):
            bot.poll_once()

        analyze.assert_called_once_with("2330")
        self.assertNotIn(123, bot.pending_analysis_chats)
        self.assertIn("多空分析", bot.send_message.call_args.args[1])
        self.assertIn("非投資建議", bot.send_message.call_args.args[1])

    def test_ai_analysis_command_surfaces_provider_configuration_error(self):
        bot = TelegramBotHandler(token="test-token")
        bot.send_message = Mock()

        with patch(
            "gvw_bot.bot_handler.analyze_stock",
            side_effect=RuntimeError("尚未設定 OPENAI_API_KEY"),
        ) as analyze:
            bot.handle_command(123, "/analyze", "/analyze 2330")

        analyze.assert_called_once_with("2330")
        self.assertIn("OPENAI_API_KEY", bot.send_message.call_args.args[1])

    def setUp(self):
        self.test_hb_file = os.path.join(os.path.dirname(__file__), "test_heartbeat.json")
        self.hb = HeartbeatManager(heartbeat_file=self.test_hb_file)

    def tearDown(self):
        if os.path.exists(self.test_hb_file):
            try:
                os.remove(self.test_hb_file)
            except Exception:
                pass

    def test_heartbeat_lifecycle(self):
        self.hb.write_heartbeat(status="running")
        status = self.hb.check_health(max_lag_seconds=60)
        self.assertTrue(status.get("healthy"))
        self.assertEqual(status["data"]["status"], "running")

    def test_job_trigger_logic(self):
        job = ScheduledJob(
            name="test_job",
            func=lambda: True,
            interval_minutes=5,
            weekdays_only=False
        )
        now = datetime.now()
        # Should run because last_run is None
        self.assertTrue(job.should_run(now))

        job.last_run = now
        # Should not run immediately after
        self.assertFalse(job.should_run(now))

    def test_scheduler_jobs_setup(self):
        scheduler = GVWScheduler(heartbeat_mgr=self.hb)
        job_names = [j.name for j in scheduler.jobs]
        self.assertIn("intraday_monitor", job_names)
        self.assertIn("daily_collection", job_names)
        self.assertIn("daily_ranking", job_names)
        self.assertIn("daily_report", job_names)
        periodic_sync = next(job for job in scheduler.jobs if job.name == "periodic_sync")
        self.assertEqual(periodic_sync.interval_minutes, 60)

if __name__ == "__main__":
    unittest.main()
