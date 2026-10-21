import os
import logging
import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from gvw_bot.heartbeat import HeartbeatManager
from gvw_bot.main import CredentialRedactionFilter
from gvw_bot.bot_handler import PUBLIC_REPLY_KEYBOARD, REPLY_KEYBOARD, TelegramBotHandler
from gvw_bot.scheduler import GVWScheduler, ScheduledJob
from src.state.ai_access import AIAnalysisAccessStore
from src.state.ai_provider import AIProviderPreferenceStore

class TestBotAndScheduler(unittest.TestCase):

    def test_log_filter_redacts_telegram_and_openai_credentials(self):
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "request failed: %s %s %s",
            (
                "https://api.telegram.org/bot123456:telegram-secret/sendMessage",
                "Bearer openai-secret",
                "sk-proj-openai-secret",
            ),
            None,
        )

        self.assertTrue(CredentialRedactionFilter().filter(record))
        self.assertNotIn("telegram-secret", record.getMessage())
        self.assertNotIn("openai-secret", record.getMessage())
        self.assertIn("[REDACTED]", record.getMessage())

    def test_startup_modules_import(self):
        import gvw_bot.bot_handler
        import src.persistence.sqlite_repository

    def test_bot_sends_persistent_keyboard(self):
        bot = TelegramBotHandler(token="test-token")
        response = Mock(status_code=200)

        with patch("gvw_bot.bot_handler.requests.post", return_value=response) as post:
            self.assertTrue(bot.send_message(123, "Menu"))

        self.assertEqual(post.call_args.kwargs["json"]["reply_markup"], PUBLIC_REPLY_KEYBOARD)

    def test_owner_receives_system_keyboard(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="123")
        response = Mock(status_code=200)

        with patch("gvw_bot.bot_handler.requests.post", return_value=response) as post:
            self.assertTrue(bot.send_message(123, "Menu"))

        self.assertEqual(post.call_args.kwargs["json"]["reply_markup"], REPLY_KEYBOARD)

    def test_command_menu_limits_system_commands_to_owner(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="123")
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"ok": True}

        with patch("gvw_bot.bot_handler.requests.post", return_value=response) as post:
            bot.register_commands()

        self.assertEqual(post.call_count, 2)
        public_payload = post.call_args_list[0].kwargs["json"]
        owner_payload = post.call_args_list[1].kwargs["json"]
        self.assertEqual(
            [command["command"] for command in public_payload["commands"]],
            ["analyze", "request_ai"],
        )
        self.assertEqual(owner_payload["scope"], {"type": "chat", "chat_id": 123})
        self.assertIn("collect", [command["command"] for command in owner_payload["commands"]])

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
                        "chat": {"id": 123, "type": "private"},
                        "from": {"id": 456},
                    },
                }]
            },
        )

        with patch("gvw_bot.bot_handler.requests.get", return_value=response):
            bot.poll_once()

        bot.handle_command.assert_called_once_with(
            123,
            "📊 選股報告 / Top 10",
            "📊 選股報告 / Top 10",
            user_id=456,
            chat_type="private",
            user={"id": 456},
        )

    def test_ai_analysis_keyboard_prompts_for_ticker_then_analyzes_it(self):
        self.ai_store.request(123, 123)
        self.ai_store.decide(123, approved=True)
        bot = TelegramBotHandler(token="test-token", ai_access_store=self.ai_store)
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.analyze_stock", return_value="多空分析") as analyze:
            bot.handle_command(123, "🤖 AI 個股分析", "🤖 AI 個股分析", user_id=123)

            analyze.assert_not_called()

        self.assertIn(123, bot.pending_analysis_chats)
        self.assertIn("請輸入 4 至 6 位數", bot.send_message.call_args.args[1])

        response = Mock(
            status_code=200,
            json=lambda: {
                "result": [{
                    "update_id": 1,
                    "message": {
                        "text": "2330",
                        "chat": {"id": 123, "type": "private"},
                        "from": {"id": 123},
                    },
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

    def test_ai_analysis_rejects_chat_not_in_allowlist(self):
        bot = TelegramBotHandler(
            token="test-token",
            owner_user_id="999",
            ai_access_store=self.ai_store,
        )
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.analyze_stock") as analyze:
            bot.handle_command(
                123,
                "🤖 AI 個股分析",
                "🤖 AI 個股分析",
                user_id=123,
                user={"id": 123, "first_name": "Applicant"},
            )

        analyze.assert_not_called()
        self.assertNotIn(123, bot.pending_analysis_chats)
        self.assertTrue(self.ai_store.get_request(123))

    def test_ai_analysis_application_notifies_owner_and_owner_can_approve(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AIAnalysisAccessStore(f"{temp_dir}/access.json")
            bot = TelegramBotHandler(
                token="test-token",
                owner_user_id="10",
                ai_access_store=store,
            )
            bot.send_message = Mock(return_value=True)

            bot.handle_command(
                20,
                "🤖 AI 個股分析",
                "🤖 AI 個股分析",
                user_id=20,
                user={"id": 20, "username": "applicant", "first_name": "Test"},
            )

            self.assertEqual(store.get_request(20)["status"], "pending")
            owner_notification = next(
                call for call in bot.send_message.call_args_list if call.args[0] == "10"
            )
            self.assertEqual(owner_notification.args[0], "10")
            approval_button = owner_notification.kwargs["reply_markup"]["inline_keyboard"][0][0]
            self.assertEqual(approval_button["callback_data"], "ai_access:approve:20")

            callback_update = Mock(
                status_code=200,
                json=lambda: {
                    "result": [{
                        "update_id": 2,
                        "callback_query": {
                            "id": "callback-1",
                            "from": {"id": 10},
                            "data": "ai_access:approve:20",
                        },
                    }]
                },
            )
            with (
                patch("gvw_bot.bot_handler.requests.get", return_value=callback_update),
                patch("gvw_bot.bot_handler.requests.post") as post,
            ):
                bot.poll_once()

            self.assertTrue(store.is_approved(20))
            self.assertIn("已核准", bot.send_message.call_args.args[1])
            post.assert_called_once()

    def test_owner_does_not_need_to_apply_for_ai_access(self):
        bot = TelegramBotHandler(
            token="test-token",
            owner_user_id="10",
            ai_access_store=self.ai_store,
        )
        bot.send_message = Mock()

        bot.handle_command(10, "/request_ai", "/request_ai", user_id=10)

        self.assertIsNone(self.ai_store.get_request(10))
        bot.send_message.assert_called_once()
        self.assertIn("系統負責人", bot.send_message.call_args.args[1])

    def test_ai_request_from_non_private_chat_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AIAnalysisAccessStore(f"{temp_dir}/access.json")
            bot = TelegramBotHandler(
                token="test-token",
                owner_user_id="10",
                ai_access_store=store,
            )
            bot.send_message = Mock()

            bot.handle_command(
                -100,
                "/request_ai",
                "/request_ai",
                user_id=20,
                chat_type="group",
            )

            self.assertIsNone(store.get_request(20))
            self.assertIn("私訊", bot.send_message.call_args.args[1])

    def test_non_owner_cannot_run_system_commands(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.Orchestrator") as orchestrator:
            bot.handle_command(20, "/collect", "/collect", user_id=20)

        orchestrator.assert_not_called()
        self.assertIn("僅限系統負責人", bot.send_message.call_args.args[1])

    def test_owner_can_run_system_commands(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.run_ranking", return_value={"data": []}) as rank:
            bot.handle_command(10, "/rank", "/rank", user_id=10)

        rank.assert_called_once_with()
        self.assertIn("排名計算完成", bot.send_message.call_args.args[1])

    def test_only_owner_can_decide_an_ai_access_request(self):
        self.ai_store.request(20, 20, first_name="Applicant")
        bot = TelegramBotHandler(
            token="test-token",
            owner_user_id="10",
            ai_access_store=self.ai_store,
        )
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.requests.post") as post:
            bot.handle_callback_query({
                "id": "callback-1",
                "from": {"id": 30},
                "data": "ai_access:approve:20",
            })

        self.assertFalse(self.ai_store.is_approved(20))
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"]["text"], "僅限系統負責人操作。")

    def test_ai_analysis_command_surfaces_provider_configuration_error(self):
        self.ai_store.request(123, 123)
        self.ai_store.decide(123, approved=True)
        bot = TelegramBotHandler(token="test-token", ai_access_store=self.ai_store)
        bot.send_message = Mock()

        with (
            patch(
                "gvw_bot.bot_handler.analyze_stock",
                side_effect=RuntimeError("尚未設定 OPENAI_API_KEY"),
            ) as analyze,
        ):
            bot.handle_command(123, "/analyze", "/analyze 2330", user_id=123)

        analyze.assert_called_once_with("2330")
        self.assertIn("OPENAI_API_KEY", bot.send_message.call_args.args[1])

    def _owner_bot_with_provider_store(self):
        provider_store = AIProviderPreferenceStore(f"{self.ai_temp_dir.name}/provider.json")
        bot = TelegramBotHandler(
            token="test-token", owner_user_id="10", ai_provider_store=provider_store
        )
        bot.send_message = Mock()
        return bot, provider_store

    def test_non_owner_cannot_change_ai_provider(self):
        bot, provider_store = self._owner_bot_with_provider_store()
        before = provider_store.get_chain()

        bot.handle_command(20, "/ai_set", "/ai_set gemma/gemma-3-27b-it", user_id=20)

        self.assertEqual(provider_store.get_chain(), before)
        self.assertIn("僅限系統負責人", bot.send_message.call_args.args[1])

    def test_owner_can_manage_ai_provider_model_chain(self):
        bot, provider_store = self._owner_bot_with_provider_store()

        bot.handle_command(
            10, "/ai_set", "/ai_set gemma/gemma-3-27b-it openai/gpt-4o", user_id=10
        )
        self.assertEqual(provider_store.get_chain(), ["gemma/gemma-3-27b-it", "openai/gpt-4o"])
        self.assertIn("gemma/gemma-3-27b-it", bot.send_message.call_args.args[1])

        bot.handle_command(10, "/ai_add", "/ai_add openai/gpt-4o-mini 2", user_id=10)
        self.assertEqual(
            provider_store.get_chain(),
            ["gemma/gemma-3-27b-it", "openai/gpt-4o-mini", "openai/gpt-4o"],
        )

        bot.handle_command(10, "/ai_remove", "/ai_remove 1", user_id=10)
        self.assertEqual(provider_store.get_chain(), ["openai/gpt-4o-mini", "openai/gpt-4o"])

        bot.handle_command(10, "/ai_set", "/ai_set openai/not-a-model", user_id=10)
        self.assertEqual(provider_store.get_chain(), ["openai/gpt-4o-mini", "openai/gpt-4o"])
        self.assertIn("不支援 model", bot.send_message.call_args.args[1])

        bot.handle_command(10, "/ai_models", "/ai_models", user_id=10)
        self.assertIn("gemma/gemma-3-27b-it", bot.send_message.call_args.args[1])

        bot.handle_command(10, "/ai_status", "/ai_status", user_id=10)
        self.assertIn("1. <code>openai/gpt-4o-mini</code>（主力）", bot.send_message.call_args.args[1])

    def test_ai_check_moves_healthy_entries_first_when_primary_is_down(self):
        bot, provider_store = self._owner_bot_with_provider_store()
        results = [
            {"entry": "openai/gpt-4o-mini", "ok": False, "detail": "HTTP 429"},
            {"entry": "gemma/gemma-3-27b-it", "ok": True, "detail": "120ms"},
        ]

        with patch("gvw_bot.bot_handler.check_providers", return_value=results):
            bot.handle_command(10, "/ai_check", "/ai_check", user_id=10)

        self.assertEqual(
            provider_store.get_chain(), ["gemma/gemma-3-27b-it", "openai/gpt-4o-mini"]
        )
        self.assertIn("新主力", bot.send_message.call_args.args[1])

    def setUp(self):
        self.test_hb_file = os.path.join(os.path.dirname(__file__), "test_heartbeat.json")
        self.hb = HeartbeatManager(heartbeat_file=self.test_hb_file)
        self.ai_temp_dir = tempfile.TemporaryDirectory()
        self.ai_store = AIAnalysisAccessStore(f"{self.ai_temp_dir.name}/access.json")

    def tearDown(self):
        self.ai_temp_dir.cleanup()
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
