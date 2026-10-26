import os
import time
import logging
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from gvw_bot.heartbeat import HeartbeatManager
from gvw_bot.main import CredentialRedactionFilter
from gvw_bot.bot_handler import PUBLIC_REPLY_KEYBOARD, REPLY_KEYBOARD, TelegramBotHandler
from gvw_bot.scheduler import GVWScheduler, ScheduledJob
from src.state.ai_access import AIAnalysisAccessStore
from src.state.ai_provider import AIProviderPreferenceStore
from src.state.alert_subscribers import AlertSubscriptionStore

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
        import importlib
        self.assertIsNotNone(importlib.import_module("gvw_bot.bot_handler"))
        self.assertIsNotNone(importlib.import_module("src.persistence.sqlite_repository"))

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

        # Owner receives same clean keyboard layout as regular users (with AI analysis enabled)
        self.assertEqual(post.call_args.kwargs["json"]["reply_markup"], REPLY_KEYBOARD)

    def test_command_menu_limits_system_commands_to_owner(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="123")
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"ok": True}

        with patch("gvw_bot.bot_handler.requests.post", return_value=response) as post:
            bot.register_commands()

        calls = post.call_args_list
        urls = [c.args[0] for c in calls]
        # Public scope deletes commands and resets menu button
        self.assertTrue(any("deleteMyCommands" in u for u in urls))
        self.assertTrue(any("setChatMenuButton" in u for u in urls))
        # Owner chat scope gets full BOT_COMMANDS
        self.assertTrue(any("setMyCommands" in u for u in urls))
        owner_payload = next(c.kwargs["json"] for c in calls if "setMyCommands" in c.args[0])
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

    def test_non_owner_can_run_report_and_top10(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.generate_daily_report", return_value="Daily Report Content"):
            bot.handle_command(20, "/report", "/report", user_id=20)
            bot.handle_command(20, "/today", "/today", user_id=20)
            bot.handle_command(20, "/top10", "/top10", user_id=20)
            bot.handle_command(20, "🏆 Top 10 選股", "🏆 Top 10 選股", user_id=20)

        self.assertEqual(bot.send_message.call_count, 8)
        for call in bot.send_message.call_args_list:
            self.assertNotIn("僅限系統負責人", call.args[1])

    def test_toggle_monitor_subscribes_and_unsubscribes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertSubscriptionStore(f"{temp_dir}/alerts.json")
            bot = TelegramBotHandler(token="test-token", alert_store=store)
            bot.send_message = Mock()

            # 1. Toggle ON
            bot.handle_command(123, "/toggle_monitor", "/toggle_monitor", user_id=123)
            self.assertTrue(store.is_subscribed(123))
            self.assertIn("已開啟盤中監控", bot.send_message.call_args.args[1])

            # 2. Toggle OFF via button text
            bot.handle_command(123, "🔔 盤中監控", "🔔 盤中監控", user_id=123)
            self.assertFalse(store.is_subscribed(123))
            self.assertIn("已關閉盤中監控", bot.send_message.call_args.args[1])

    def test_dynamic_keyboard_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            alert_store = AlertSubscriptionStore(f"{temp_dir}/alerts.json")
            ai_store = AIAnalysisAccessStore(f"{temp_dir}/access.json")
            bot = TelegramBotHandler(
                token="test-token",
                owner_user_id="10",
                alert_store=alert_store,
                ai_access_store=ai_store,
            )

            # Unapproved regular user (not subscribed)
            kb = bot._keyboard_for_chat(20, user_id=20)
            self.assertEqual(kb["keyboard"], [
                ["🏆 Top 10 選股", "🔕 盤中監控"],
                ["📝 申請 AI 分析"],
            ])

            # Subscribed regular user
            alert_store.subscribe(20)
            kb = bot._keyboard_for_chat(20, user_id=20)
            self.assertEqual(kb["keyboard"], [
                ["🏆 Top 10 選股", "🔔 盤中監控"],
                ["📝 申請 AI 分析"],
            ])

            # Approved user
            ai_store.request(20, 20)
            ai_store.decide(20, approved=True)
            kb = bot._keyboard_for_chat(20, user_id=20)
            self.assertEqual(kb["keyboard"], [
                ["🏆 Top 10 選股", "🔔 盤中監控"],
                ["🤖 AI 個股分析"],
            ])

            # Owner
            kb = bot._keyboard_for_chat(10, user_id=10)
            self.assertEqual(kb["keyboard"], [
                ["🏆 Top 10 選股", "🔕 盤中監控"],
                ["🤖 AI 個股分析"],
            ])

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

    def test_ai_check_formats_two_tier_and_probing_details(self):
        bot, provider_store = self._owner_bot_with_provider_store()
        results = [
            {
                "entry": "openai/gpt-4o",
                "label": "OpenAI",
                "ok": False,
                "detail": "HTTP 401",
                "probe_status": "HTTP 401 (認證失敗，金鑰無效)",
                "tier1_results": [{"model": "gpt-4o", "ok": False, "detail": "HTTP 401"}],
            },
            {
                "entry": "gemma/gemma-3-27b-it",
                "label": "Google Gemma",
                "ok": True,
                "detail": "150ms",
                "probe_status": "探測成功 (3 個可用模型)",
                "tier1_results": [
                    {"model": "gemini-1.5-flash", "ok": True, "detail": "150ms"}
                ],
            },
        ]

        with patch("gvw_bot.bot_handler.check_providers", return_value=results):
            bot.handle_command(10, "/ai_check", "/ai_check", user_id=10)

        response_text = bot.send_message.call_args.args[1]
        self.assertIn("模型探測", response_text)
        self.assertIn("第一層 (內部模型備援)", response_text)
        self.assertIn("gemini-1.5-flash", response_text)
        self.assertIn("新主力", response_text)

    def test_owner_can_select_ai_model_via_callback_query(self):
        bot, provider_store = self._owner_bot_with_provider_store()
        bot._answer_callback_query = Mock()

        bot.handle_callback_query({
            "id": "cb-ai-select",
            "from": {"id": 10},
            "data": "ai_select:local/deepseek/deepseek-r1-0528-qwen3-8b",
        })

        self.assertEqual(provider_store.get_chain()[0], "local/deepseek/deepseek-r1-0528-qwen3-8b")
        bot._answer_callback_query.assert_called_once()
        self.assertIn("已將主力 AI 模型切換為", bot.send_message.call_args.args[1])

    def test_owner_can_view_and_set_local_ai_endpoint(self):
        bot, provider_store = self._owner_bot_with_provider_store()

        bot.handle_command(10, "/ai_local", "/ai_local", user_id=10)
        self.assertIn("地端 LM-Studio 端點", bot.send_message.call_args.args[1])
        self.assertIn("目前設定", bot.send_message.call_args.args[1])

        bot.handle_command(10, "/ai_local", "/ai_local http://192.168.1.100:1234/v1", user_id=10)
        self.assertEqual(provider_store.get_local_base_url(), "http://192.168.1.100:1234/v1")
        self.assertIn("已更新", bot.send_message.call_args.args[1])

        bot.handle_command(20, "/ai_local", "/ai_local http://bad.com", user_id=20)
        self.assertEqual(provider_store.get_local_base_url(), "http://192.168.1.100:1234/v1")
        self.assertIn("僅限系統負責人", bot.send_message.call_args.args[1])

    def test_non_owner_cannot_run_update(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot.send_message = Mock()

        bot.handle_command(20, "/update", "/update", user_id=20)
        self.assertIn("僅限系統負責人", bot.send_message.call_args.args[1])
        self.assertFalse(bot._updating)

    def test_owner_update_when_already_latest(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.subprocess.run") as mock_run, \
             patch("gvw_bot.bot_handler.subprocess.check_output") as mock_check_output:
            mock_run.return_value = Mock(returncode=0, stderr="")
            mock_check_output.side_effect = [
                "1234567abcdef",  # local_rev
                "1234567abcdef",  # remote_rev
            ]

            bot._execute_system_update(10)

        calls = [c.args[1] for c in bot.send_message.call_args_list]
        self.assertTrue(any("最新版本" in c for c in calls))
        self.assertFalse(bot._updating)

    def test_owner_update_pulls_and_restarts(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.subprocess.run") as mock_run, \
             patch("gvw_bot.bot_handler.subprocess.check_output") as mock_check_output, \
             patch("gvw_bot.bot_handler.subprocess.Popen") as mock_popen, \
             patch("gvw_bot.bot_handler.time.sleep") as mock_sleep, \
             patch("gvw_bot.bot_handler.os.path.exists", return_value=False):
            mock_run.return_value = Mock(returncode=0, stderr="")
            mock_check_output.side_effect = [
                "1111111oldrev",  # local_rev
                "2222222newrev",  # remote_rev
                "2222222 feat: new update",  # git log
                "2222222newrev",  # new HEAD
            ]

            bot._execute_system_update(10)

        calls = [c.args[1] for c in bot.send_message.call_args_list]
        self.assertTrue(any("系統代碼更新成功" in c for c in calls))
        self.assertTrue(any("正在重啟" in c for c in calls))
        mock_popen.assert_called_once_with(["systemctl", "--user", "restart", "gvw_bot"])
        self.assertFalse(bot._updating)

    def test_owner_update_handles_git_fetch_failure(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot.send_message = Mock()

        with patch("gvw_bot.bot_handler.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stderr="fatal: unable to access")

            bot._execute_system_update(10)

        calls = [c.args[1] for c in bot.send_message.call_args_list]
        self.assertTrue(any("Git fetch 失敗" in c for c in calls))
        self.assertFalse(bot._updating)

    def test_get_git_commit_hash(self):
        from gvw_bot.main import get_git_commit_hash
        with patch("gvw_bot.main.subprocess.check_output", return_value="abcdef123456\n"):
            self.assertEqual(get_git_commit_hash(), "abcdef1")
        with patch("gvw_bot.main.subprocess.check_output", side_effect=Exception("git error")):
            self.assertEqual(get_git_commit_hash(), "unknown")

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
                # Ignore cleanup error if file was already removed
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

    def test_job_trigger_logic_daily_time(self):
        job = ScheduledJob(
            name="daily_test",
            func=lambda: True,
            daily_time="14:00",
            weekdays_only=True,
            grace_minutes=30
        )
        # Wednesday 13:59:00: before target time
        t_before = datetime(2026, 10, 21, 13, 59, 0)
        self.assertFalse(job.should_run(t_before))

        # Wednesday 14:00:00: target time
        t_exact = datetime(2026, 10, 21, 14, 0, 0)
        self.assertTrue(job.should_run(t_exact))

        # Wednesday 14:15:00: within grace window (not yet run today)
        t_grace = datetime(2026, 10, 21, 14, 15, 0)
        self.assertTrue(job.should_run(t_grace))

        # If already run earlier today: should not run
        job.last_run = datetime(2026, 10, 21, 14, 0, 10)
        self.assertFalse(job.should_run(t_grace))

        # Wednesday 14:31:00: past grace window
        job.last_run = None
        t_past = datetime(2026, 10, 21, 14, 31, 0)
        self.assertFalse(job.should_run(t_past))

        # Saturday 14:10:00: weekend with weekdays_only=True
        t_weekend = datetime(2026, 10, 24, 14, 10, 0)
        self.assertFalse(job.should_run(t_weekend))

    def test_job_is_running_guard(self):
        job = ScheduledJob(
            name="test_guard",
            func=lambda: True,
            interval_minutes=5,
            weekdays_only=False
        )
        now = datetime.now()
        self.assertTrue(job.should_run(now))

        job.is_running = True
        self.assertFalse(job.should_run(now))

    def test_trigger_job_async_execution(self):
        scheduler = GVWScheduler(heartbeat_mgr=self.hb)
        executed = threading.Event()

        def fake_job():
            executed.set()

        job = ScheduledJob(name="async_job", func=fake_job, interval_minutes=5)
        triggered = scheduler.trigger_job_async(job)
        self.assertTrue(triggered)
        self.assertTrue(executed.wait(timeout=2.0))
        # Small wait for thread to finish finally block
        time.sleep(0.05)
        self.assertFalse(job.is_running)
        self.assertIsNotNone(job.last_run)

    def test_trigger_job_async_handles_exception(self):
        scheduler = GVWScheduler(heartbeat_mgr=self.hb)
        failed = threading.Event()

        def bad_job():
            failed.set()
            raise RuntimeError("Job crash")

        job = ScheduledJob(name="failing_job", func=bad_job, interval_minutes=5)
        triggered = scheduler.trigger_job_async(job)
        self.assertTrue(triggered)
        self.assertTrue(failed.wait(timeout=2.0))
        time.sleep(0.05)
        self.assertFalse(job.is_running)
        self.assertEqual(self.hb.jobs_status["failing_job"]["status"], "error")

    def test_jobs_command_shows_status(self):
        scheduler = GVWScheduler(heartbeat_mgr=self.hb)
        bot = TelegramBotHandler(token="test-token", owner_user_id="10", scheduler=scheduler)
        bot.send_message = Mock()
        bot.handle_command(10, "/jobs", "/jobs", user_id=10)
        bot.send_message.assert_called_once()
        msg = bot.send_message.call_args.args[1]
        self.assertIn("intraday_monitor", msg)
        self.assertIn("daily_collection", msg)
        self.assertIn("尚未執行", msg)

    def test_scheduler_jobs_setup(self):
        scheduler = GVWScheduler(heartbeat_mgr=self.hb)
        job_names = [j.name for j in scheduler.jobs]
        self.assertIn("intraday_monitor", job_names)
        self.assertIn("daily_collection", job_names)
        self.assertIn("daily_ranking", job_names)
        self.assertIn("daily_report", job_names)
        self.assertIn("morning_briefing", job_names)

        daily_collection = next(job for job in scheduler.jobs if job.name == "daily_collection")
        self.assertEqual(daily_collection.grace_minutes, 45)

        daily_report = next(job for job in scheduler.jobs if job.name == "daily_report")
        self.assertEqual(daily_report.grace_minutes, 60)

        periodic_sync = next(job for job in scheduler.jobs if job.name == "periodic_sync")
        self.assertEqual(periodic_sync.interval_minutes, 60)
        self.assertIsNotNone(periodic_sync.last_run)
        self.assertFalse(periodic_sync.should_run(datetime.now()))

    def test_intraday_monitor_broadcasts_to_subscribers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertSubscriptionStore(f"{temp_dir}/alerts.json")
            store.subscribe("100")
            store.subscribe("200")

            scheduler = GVWScheduler(heartbeat_mgr=self.hb, alert_store=store)
            with (
                patch("gvw_bot.scheduler.run_intraday_monitoring", return_value=["🚀 2330 突破!"]),
                patch("gvw_bot.scheduler.send_telegram_message") as mock_send,
            ):
                scheduler.job_intraday_monitor()

            calls = mock_send.call_args_list
            # 盤中監控不得推播至預設頻道，僅推播給有開啟盤中監控的使用者
            home_calls = [c for c in calls if "chat_id" not in c.kwargs or c.kwargs["chat_id"] is None]
            self.assertEqual(len(home_calls), 0)

            sub_targets = {str(c.kwargs.get("chat_id")) for c in calls if c.kwargs.get("chat_id") is not None}
            self.assertEqual(sub_targets, {"100", "200"})

    def test_plain_ticker_triggers_stock_lookup_for_regular_user(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot.send_message = Mock()

        bot.handle_command(123, "2330", "2330", user_id=123, chat_type="private")

        calls = [c.args[1] for c in bot.send_message.call_args_list]
        self.assertTrue(any("正在查詢 2330" in c for c in calls))
        self.assertTrue(any("個股行情與財務估值" in c for c in calls))

    def test_regular_user_can_use_callback_query_to_request_ai(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        bot._answer_callback_query = Mock()
        bot.send_message = Mock()

        bot.handle_callback_query({
            "id": "cb-ai-req",
            "from": {"id": 123, "first_name": "Alice"},
            "message": {"chat": {"id": 123}},
            "data": "ai_request_access:123",
        })

        bot._answer_callback_query.assert_called_once_with("cb-ai-req", "已送出 AI 申請")

    def test_job_trigger_logic_weekly_day(self):
        # Weekly job set for Sunday at 20:00
        job = ScheduledJob(
            name="weekly_audit_test",
            func=lambda: True,
            daily_time="20:00",
            weekly_day=6,  # Sunday
            weekdays_only=False,
            grace_minutes=60,
        )
        # 2026-10-25 is a Sunday (weekday = 6)
        # Sunday 19:55:00: before target time
        t_before = datetime(2026, 10, 25, 19, 55, 0)
        self.assertFalse(job.should_run(t_before))

        # Sunday 20:00:00: exact time
        t_exact = datetime(2026, 10, 25, 20, 0, 0)
        self.assertTrue(job.should_run(t_exact))

        # Sunday 20:45:00: within grace window
        t_grace = datetime(2026, 10, 25, 20, 45, 0)
        self.assertTrue(job.should_run(t_grace))

        # Sunday 20:45:00 if already run today: should not run again
        job.last_run = datetime(2026, 10, 25, 20, 0, 5)
        self.assertFalse(job.should_run(t_grace))

        # Sunday 21:05:00: past grace window
        job.last_run = None
        t_past = datetime(2026, 10, 25, 21, 5, 0)
        self.assertFalse(job.should_run(t_past))

        # Monday 20:10:00 (weekday = 0): wrong day of week
        t_monday = datetime(2026, 10, 26, 20, 10, 0)
        self.assertFalse(job.should_run(t_monday))

    def test_weekly_github_audit_creates_issue_when_problems_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertSubscriptionStore(f"{temp_dir}/alerts.json")
            store.subscribe("12345")
            scheduler = GVWScheduler(heartbeat_mgr=self.hb, alert_store=store)

            fake_audit_result = {
                "has_problems": True,
                "summary": {"alerts_count": 2, "failures_count": 1, "open_issues_count": 0},
                "alerts": [{"rule_id": "py/test", "severity": "high"}],
                "workflow_failures": [{"name": "CI", "url": "https://github.com/test"}],
                "open_issues": [],
                "week_tag": "2026-W43",
            }

            with (
                patch("src.diagnostics.github_auditor.GitHubAuditor.run_full_audit", return_value=fake_audit_result),
                patch("src.diagnostics.github_auditor.GitHubAuditor.create_or_update_weekly_issue", return_value=(True, "https://github.com/test/issues/100")) as mock_issue,
                patch("gvw_bot.scheduler.send_telegram_message", return_value=True) as mock_send,
            ):
                scheduler.job_weekly_github_audit()

            mock_issue.assert_called_once()
            self.assertTrue(mock_send.called)
            sent_text = mock_send.call_args[0][0]
            self.assertIn("https://github.com/test/issues/100", sent_text)
            self.assertIn("CodeQL 弱點警報: <b>2</b>", sent_text)

    def test_weekly_github_audit_greenlight_when_no_problems(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertSubscriptionStore(f"{temp_dir}/alerts.json")
            store.subscribe("12345")
            scheduler = GVWScheduler(heartbeat_mgr=self.hb, alert_store=store)

            fake_audit_result = {
                "has_problems": False,
                "summary": {"alerts_count": 0, "failures_count": 0, "open_issues_count": 0},
                "alerts": [],
                "workflow_failures": [],
                "open_issues": [],
                "week_tag": "2026-W43",
            }

            with (
                patch("src.diagnostics.github_auditor.GitHubAuditor.run_full_audit", return_value=fake_audit_result),
                patch("src.diagnostics.github_auditor.GitHubAuditor.create_or_update_weekly_issue") as mock_issue,
                patch("gvw_bot.scheduler.send_telegram_message", return_value=True) as mock_send,
            ):
                scheduler.job_weekly_github_audit()

            # Should not create any issue
            mock_issue.assert_not_called()
            self.assertTrue(mock_send.called)
            sent_text = mock_send.call_args[0][0]
            self.assertIn("本週系統與程式碼庫檢查全數正常", sent_text)

    def test_bot_handles_audit_command(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="999")
        bot.send_message = Mock()

        fake_audit_result = {
            "has_problems": False,
            "summary": {"alerts_count": 0, "failures_count": 0, "open_issues_count": 0},
            "alerts": [],
            "workflow_failures": [],
            "open_issues": [],
            "week_tag": "2026-W43",
        }

        with patch("src.diagnostics.github_auditor.GitHubAuditor.run_full_audit", return_value=fake_audit_result):
            bot._execute_github_audit("999")

        bot.send_message.assert_called()
        self.assertIn("專案健康狀態優良", bot.send_message.call_args[0][1])

    def test_daily_report_broadcasts_to_subscribers_when_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertSubscriptionStore(f"{temp_dir}/alerts.json")
            store.subscribe("100")
            store.subscribe("200")

            scheduler = GVWScheduler(heartbeat_mgr=self.hb, alert_store=store)
            with (
                patch("gvw_bot.scheduler.generate_daily_report", return_value="Daily Report Text"),
                patch("gvw_bot.scheduler.send_telegram_message", return_value=True) as mock_send,
            ):
                scheduler.job_daily_report()

            calls = mock_send.call_args_list
            sub_targets = {str(c.kwargs.get("chat_id")) for c in calls if c.kwargs.get("chat_id") is not None}
            self.assertEqual(sub_targets, {"100", "200"})

    def test_render_ai_models_includes_copyable_ai_set_presets_and_buttons(self):
        bot = TelegramBotHandler(token="test-token", owner_user_id="999")
        bot.send_message = Mock()

        bot._render_ai_models("999")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][1]
        markup = bot.send_message.call_args[1].get("reply_markup", {})

        # Verify copyable /ai_set presets are in message text
        self.assertIn("一鍵複製 /ai_set 模型順序模板", text)
        self.assertIn("/ai_set gemma/gemini-3.5-flash-lite", text)
        self.assertIn("local/active", text)

        # Verify inline keyboard has preset buttons
        all_cb = [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]
        self.assertIn("ai_preset:lite_ladder", all_cb)
        self.assertIn("ai_preset:standard", all_cb)
        self.assertIn("ai_preset:local_first", all_cb)

    def test_callback_query_applies_ai_preset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AIProviderPreferenceStore(f"{temp_dir}/ai_pref.json")
            bot = TelegramBotHandler(token="test-token", owner_user_id="999", ai_provider_store=store)
            bot._answer_callback_query = Mock()
            bot.send_message = Mock()

            bot.handle_callback_query({
                "id": "cb-preset-1",
                "from": {"id": 999},
                "data": "ai_preset:lite_ladder",
            })

            chain = store.get_chain()
            self.assertEqual(chain[0], "gemma/gemini-3.5-flash-lite")
            self.assertIn("local/active", chain)
            bot._answer_callback_query.assert_called_once_with("cb-preset-1", "已套用：⚡ Gemini Lite 梯隊備援鏈")
            bot.send_message.assert_called_once()
            self.assertIn("已套用推薦模型順序", bot.send_message.call_args[0][1])

    def test_ai_set_accepts_flash_lite_ladder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AIProviderPreferenceStore(f"{temp_dir}/ai_pref.json")
            bot = TelegramBotHandler(token="test-token", owner_user_id="999", ai_provider_store=store)

            user_cmd_text = "/ai_set gemma/gemini-3.5-flash-lite gemma/gemini-3.1-flash-lite gemma/gemini-2.5-flash-lite gemma/gemini-3.5-flash local/active"
            args = user_cmd_text.split()[1:]
            resp = bot._update_ai_chain("/ai_set", args)

            self.assertIn("✅ 已更新", resp)
            chain = store.get_chain()
            self.assertEqual(chain, [
                "gemma/gemini-3.5-flash-lite",
                "gemma/gemini-3.1-flash-lite",
                "gemma/gemini-2.5-flash-lite",
                "gemma/gemini-3.5-flash",
                "local/active",
            ])

    def test_ai_dashboard_and_policy_switch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AIProviderPreferenceStore(f"{temp_dir}/ai_pref.json")
            bot = TelegramBotHandler(token="test-token", owner_user_id="999", ai_provider_store=store)
            bot.send_message = Mock()

            # Non-owner blocked
            bot.handle_command(10, "/ai", "/ai", user_id=10)
            self.assertIn("僅限系統負責人", bot.send_message.call_args[0][1])

            # Owner views dashboard
            bot.send_message.reset_mock()
            bot.handle_command(999, "/ai", "/ai", user_id=999)
            bot.send_message.assert_called_once()
            dashboard_text = bot.send_message.call_args[0][1]
            markup = bot.send_message.call_args[1].get("reply_markup", {})
            self.assertIn("GVW AI 智慧控制中心", dashboard_text)
            self.assertIn("當前策略", dashboard_text)
            self.assertTrue(any("ai_policy:auto" in str(row) for row in markup.get("inline_keyboard", [])))

            # Owner switches policy via command: /ai gemini
            bot.send_message.reset_mock()
            bot.handle_command(999, "/ai", "/ai gemini", user_id=999)
            self.assertEqual(store.get_policy(), "gemini")
            self.assertIn("Gemini 優先階梯", bot.send_message.call_args[0][1])

            # Callback query switches policy to openai
            bot.send_message.reset_mock()
            bot._answer_callback_query = Mock()
            bot.handle_callback_query({
                "id": "cb-policy-1",
                "from": {"id": 999},
                "data": "ai_policy:openai",
                "message": {"chat": {"id": 999}},
            })
            self.assertEqual(store.get_policy(), "openai")
            bot._answer_callback_query.assert_called_once_with("cb-policy-1", "已套用：⚡ OpenAI 優先階梯")
            self.assertIn("OpenAI 優先階梯", bot.send_message.call_args[0][1])

if __name__ == "__main__":
    unittest.main()
