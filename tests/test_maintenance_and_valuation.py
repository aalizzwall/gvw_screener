import os
import json
import time
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.agent.maintenance_agent import MaintenanceAgent
from src.agent.tool_registry import ToolRegistry
from src.agent.session_context import SessionContext
from src.providers.twse_adapter import TWSEAdapter
from src.providers.valuation_service import ValuationService
from src.providers.factory import DataProviderFactory
from src.reporting.report_engine import get_financials
from src.analysis.ai_analyst import _stock_snapshot
from gvw_bot.bot_handler import TelegramBotHandler, format_telegram_html
from src.state.state_manager import StateManager
from src.state.ai_provider import AIProviderPreferenceStore


class TestMaintenanceAgent(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.temp_dir.name, "data")
        self.logs_dir = os.path.join(self.temp_dir.name, "logs")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)

        self.state = StateManager(data_dir=self.data_dir)
        self.ai_store = AIProviderPreferenceStore(os.path.join(self.data_dir, "ai.json"))
        self.agent = MaintenanceAgent(
            state_manager=self.state,
            ai_provider_store=self.ai_store,
            logs_dir=self.logs_dir,
            data_dir=self.data_dir,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tool_inspect_stock_not_found(self):
        res = self.agent.tool_inspect_stock("9999")
        self.assertFalse(res["found"])
        self.assertIn("無 9999", res["message"])

    def test_tool_inspect_stock_found(self):
        self.state.save_stock_data("2330", {
            "ticker": "2330",
            "source": "twse",
            "basic_info": {"name": "台積電", "industry": "半導體", "pe": 28.5},
            "financials": {"income_statement": {"net_income": 300000000}},
            "prices": [{"date": "2026-10-21", "close": 1050}],
        })
        res = self.agent.tool_inspect_stock("2330")
        self.assertTrue(res["found"])
        self.assertEqual(res["ticker"], "2330")
        self.assertEqual(res["prices_count"], 1)
        self.assertEqual(res["basic_info"]["name"], "台積電")

    def test_tool_search_logs(self):
        log_file = os.path.join(self.logs_dir, "gvw_bot.log")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("2026-10-21 INFO Starting collection\n")
            f.write("2026-10-21 ERROR TWSE endpoint timeout\n")
            f.write("2026-10-21 INFO Finished ranking\n")

        res = self.agent.tool_search_logs(keyword="TWSE", max_lines=5)
        self.assertIn("TWSE endpoint timeout", res)
        self.assertNotIn("Finished ranking", res)

    def test_tool_inspect_db_schema(self):
        db_path = os.path.join(self.data_dir, "test_gvw.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE stocks (id INT, ticker TEXT);")
        conn.commit()
        conn.close()

        schema = self.agent.tool_inspect_db_schema()
        self.assertIn("test_gvw.db", schema)
        self.assertIn("stocks", schema["test_gvw.db"])

    def test_tool_get_provider_endpoints(self):
        endpoints = self.agent.tool_get_provider_endpoints()
        self.assertIn("twse", endpoints)
        self.assertIn("finmind", endpoints)
        self.assertIn("BWIBBU_d", endpoints["twse"]["endpoints"]["valuation"])

    def test_ask_react_tool_calling_loop(self):
        # Turn 1: LLM decides to call inspect_stock("2330")
        # Turn 2: LLM receives observation and returns final_answer
        responses = [
            json.dumps({"action": "inspect_stock", "args": {"ticker": "2330"}}),
            json.dumps({"action": "final_answer", "answer": "經過查證，2330 的資料庫記錄存在。"}),
        ]
        with patch.object(self.agent, "_call_llm", side_effect=responses):
            ans = self.agent.ask("為什麼 2330 沒資料？")
            self.assertIn("2330 的資料庫記錄存在", ans)

    def test_ask_multi_turn_session_continuity(self):
        # Turn 1
        resp1 = json.dumps({"action": "final_answer", "answer": "台積電 2330 狀態正常。"})
        # Turn 2 (LLM receives previous session history)
        resp2 = json.dumps({"action": "final_answer", "answer": "聯發科 2454 狀態也已查詢。"})
        with patch.object(self.agent, "_call_llm", side_effect=[resp1, resp2]) as mock_call:
            ans1 = self.agent.ask("台積電狀況？", session_id="chat-1")
            self.assertIn("2330 狀態正常", ans1)

            ans2 = self.agent.ask("那聯發科呢？", session_id="chat-1")
            self.assertIn("2454", ans2)

            # Check that the second call's messages contained the first question
            second_call_messages = mock_call.call_args_list[1][0][0]
            contents = [m["content"] for m in second_call_messages]
            self.assertTrue(any("台積電狀況？" in c for c in contents))

        # Clear session
        self.agent.clear_session("chat-1")
        session = self.agent.get_or_create_session("chat-1")
        self.assertEqual(len(session.history), 0)

    def test_parse_json_response_with_inner_code_blocks(self):
        raw_json_with_code = (
            '{\n'
            '  "action": "final_answer",\n'
            '  "answer": "報告負責人：\\n1. **重啟指令**：\\n```bash\\nsudo systemctl --user restart gvw_bot\\n```\\n2. 請檢查 `.env`。"\n'
            '}'
        )
        parsed = self.agent._parse_json_response(raw_json_with_code)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["action"], "final_answer")
        self.assertIn("sudo systemctl --user restart gvw_bot", parsed["answer"])

    def test_parse_json_response_wrapped_in_outer_fence(self):
        wrapped = (
            '```json\n'
            '{\n'
            '  "action": "final_answer",\n'
            '  "answer": "系統健康狀態良好。"\n'
            '}\n'
            '```'
        )
        parsed = self.agent._parse_json_response(wrapped)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["action"], "final_answer")
        self.assertEqual(parsed["answer"], "系統健康狀態良好。")

    def test_ask_unwraps_malformed_json_fallback(self):
        malformed = (
            '{"action": "final_answer", "answer": "請重啟服務：\\n```bash\\nsudo systemctl restart gvw_bot\\n```"}'
        )
        with patch.object(self.agent, "_call_llm", return_value=malformed):
            ans = self.agent.ask("如何重啟？")
            self.assertNotIn('{"action": "final_answer"', ans)
            self.assertIn("sudo systemctl restart gvw_bot", ans)

    def test_format_telegram_html(self):
        markdown_text = (
            "報告負責人：\n\n"
            "1. **關於 .env 設定**：\n"
            "   - 經檢查 `/home/pi/.env` 存在\n"
            "   - 延遲小於 <500ms 正常\n"
            "2. **執行指令**：\n"
            "```bash\n"
            "sudo systemctl --user restart gvw_bot\n"
            "```\n"
        )
        html_out = format_telegram_html(markdown_text)
        self.assertIn("<b>關於 .env 設定</b>", html_out)
        self.assertIn("<code>/home/pi/.env</code>", html_out)
        self.assertIn("&lt;500ms", html_out)
        self.assertIn("<pre><code>sudo systemctl --user restart gvw_bot</code></pre>", html_out)

    def test_format_telegram_html_unwraps_raw_json(self):
        raw_json_text = (
            '{\n'
            '  "action": "final_answer",\n'
            '  "answer": "系統排查完畢，**一切正常**。"\n'
            '}'
        )
        html_out = format_telegram_html(raw_json_text)
        self.assertNotIn('"action"', html_out)
        self.assertIn("<b>一切正常</b>", html_out)

    def test_tool_inspect_scheduler_jobs(self):
        mock_job = Mock()
        mock_job.name = "test_intraday_job"
        mock_job.interval_minutes = 15
        mock_job.daily_time = None
        mock_job.weekdays_only = True
        mock_job.is_running = False
        mock_job.last_run = None

        mock_scheduler = Mock()
        mock_scheduler.jobs = [mock_job]

        agent_with_sched = MaintenanceAgent(
            state_manager=self.state,
            ai_provider_store=self.ai_store,
            scheduler=mock_scheduler,
            logs_dir=self.logs_dir,
            data_dir=self.data_dir,
        )
        res = agent_with_sched.tool_inspect_scheduler_jobs()
        self.assertTrue(res["available"])
        self.assertEqual(res["job_count"], 1)
        self.assertEqual(res["jobs"][0]["name"], "test_intraday_job")

    def test_tool_inspect_ai_config(self):
        res = self.agent.tool_inspect_ai_config()
        self.assertIn("provider_chain", res)
        self.assertIn("primary_provider", res)

    def test_tool_registry_registration_and_execution(self):
        registry = ToolRegistry()

        @registry.register(name="add", description="加法運算")
        def add(a: int, b: int) -> int:
            return a + b

        self.assertIsNotNone(registry.get("add"))
        self.assertEqual(registry.execute("add", {"a": 2, "b": 3}), 5)

        # Non-existent tool
        err = registry.execute("subtract", {"a": 5, "b": 2})
        self.assertEqual(err["status"], "error")
        self.assertIn("未知工具名稱", err["error"])

    def test_session_context_compaction(self):
        session = SessionContext(session_id="test", max_turns=2, max_chars=100)
        session.add_user_message("msg 1")
        session.add_assistant_message("reply 1")
        session.add_user_message("msg 2")
        session.add_assistant_message("reply 2")
        session.add_user_message("msg 3")
        session.add_assistant_message("reply 3")

        # Must have compacted to max_turns
        self.assertLessEqual(session.turn_count, 2)


class TestTWSEValuationData(unittest.TestCase):
    def test_twse_adapter_fetches_pe_pb_yield(self):
        adapter = TWSEAdapter()

        mock_pl = [{"公司代號": "2330", "營業收入": "1000", "本期淨利": "500"}]
        mock_bs = [{"公司代號": "2330", "資產總額": "5000", "權益總額": "3000"}]
        mock_info = [{"公司代號": "2330", "公司名稱": "台積電", "產業類別": "半導體"}]
        mock_val = [{"Code": "2330", "Name": "台積電", "PEratio": "28.5", "PBratio": "6.2", "DividendYield": "1.8"}]

        def mock_get_data(dataset_key, params=None):
            if dataset_key == "financials_pl":
                return mock_pl
            elif dataset_key == "financials_bs":
                return mock_bs
            elif dataset_key == "basic_info":
                return mock_info
            elif dataset_key == "valuation":
                return mock_val
            return []

        adapter._get_data = Mock(side_effect=mock_get_data)

        data = adapter.fetch_stock_data("2330")
        self.assertIn("basic_info", data)
        info = data["basic_info"]
        self.assertEqual(info["pe"], 28.5)
        self.assertEqual(info["pb"], 6.2)
        self.assertEqual(info["yield"], 1.8)


class TestTelegramBotMaintenanceMode(unittest.TestCase):
    def setUp(self):
        self.bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        self.bot.send_message = Mock()

    def test_non_owner_cannot_enter_maintain_mode(self):
        self.bot.handle_command(20, "/maintain", "/maintain", user_id=20)
        self.assertIn("僅限系統負責人", self.bot.send_message.call_args.args[1])
        self.assertNotIn(20, self.bot.maintenance_mode_chats)

    def test_owner_can_toggle_maintain_mode(self):
        # Enter maintenance mode
        self.bot.handle_command(10, "/maintain", "/maintain", user_id=10)
        self.assertIn(10, self.bot.maintenance_mode_chats)
        calls = [c.args[1] for c in self.bot.send_message.call_args_list]
        self.assertTrue(any("已進入【系統維護模式】" in c for c in calls))

        # Exit maintenance mode via /maintain again
        self.bot.handle_command(10, "/maintain", "/maintain", user_id=10)
        self.assertNotIn(10, self.bot.maintenance_mode_chats)
        calls = [c.args[1] for c in self.bot.send_message.call_args_list]
        self.assertTrue(any("已退出【系統維護模式】" in c for c in calls))

    def test_maintenance_mode_processes_natural_language_query(self):
        # Put into maintenance mode
        self.bot.maintenance_mode_chats.add(10)

        with patch.object(self.bot.maintenance_agent, "ask", return_value="經診斷，系統正常。") as mock_ask:
            self.bot.handle_command(10, "為什麼 PB PE 是空白的？", "為什麼 PB PE 是空白的？", user_id=10)

            # Wait briefly for daemon thread
            time.sleep(0.1)

            mock_ask.assert_called_once_with("為什麼 PB PE 是空白的？", session_id="10")
            calls = [c.args[1] for c in self.bot.send_message.call_args_list]
            self.assertTrue(any("分析您的問題" in c for c in calls))
            self.assertTrue(any("經診斷，系統正常。" in c for c in calls))

    def test_maintenance_mode_exit_keyword(self):
        self.bot.maintenance_mode_chats.add(10)
        self.bot.handle_command(10, "exit", "exit", user_id=10)
        self.assertNotIn(10, self.bot.maintenance_mode_chats)
        calls = [c.args[1] for c in self.bot.send_message.call_args_list]
        self.assertTrue(any("已退出【系統維護模式】" in c for c in calls))

    def test_maintenance_mode_reset_keyword(self):
        self.bot.maintenance_mode_chats.add(10)
        with patch.object(self.bot.maintenance_agent, "clear_session") as mock_clear:
            self.bot.handle_command(10, "reset", "reset", user_id=10)
            self.assertIn(10, self.bot.maintenance_mode_chats)
            mock_clear.assert_called_once_with("10")
            calls = [c.args[1] for c in self.bot.send_message.call_args_list]
            self.assertTrue(any("已重置維護對話記憶" in c for c in calls))


class TestValuationServiceAndIntegration(unittest.TestCase):
    def setUp(self):
        self.service = ValuationService(cache_ttl_seconds=3600)

    @patch("src.providers.valuation_service.requests.get")
    def test_valuation_service_twse_and_tpex(self, mock_get):
        # Mock TWSE response
        twse_resp = Mock(status_code=200)
        twse_resp.json.return_value = [
            {"Code": "2330", "Name": "台積電", "PEratio": "28.05", "PBratio": "9.76", "DividendYield": "0.91", "Date": "20261021"},
            {"Code": "1101", "Name": "台泥", "PEratio": "", "PBratio": "0.87", "DividendYield": "2.99", "Date": "20261021"},
        ]
        # Mock TPEx response
        tpex_resp = Mock(status_code=200)
        tpex_resp.json.return_value = [
            {"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "PriceEarningRatio": "42.72", "PriceBookRatio": "4.35", "YieldRatio": "0.88", "Date": "1151021"}
        ]
        mock_get.side_effect = [twse_resp, tpex_resp]

        # 1. TWSE normal profitable stock
        v2330 = self.service.get_valuation("2330")
        self.assertIsNotNone(v2330)
        self.assertEqual(v2330["pe"], 28.05)
        self.assertEqual(v2330["pb"], 9.76)
        self.assertEqual(v2330["yield"], 0.91)
        self.assertFalse(v2330["is_loss"])
        self.assertEqual(v2330["source"], "twse")

        # 2. TWSE loss stock (empty PEratio, pb > 0)
        v1101 = self.service.get_valuation("1101")
        self.assertIsNotNone(v1101)
        self.assertEqual(v1101["pe"], 0.0)
        self.assertEqual(v1101["pb"], 0.87)
        self.assertEqual(v1101["yield"], 2.99)
        self.assertTrue(v1101["is_loss"])

        # 3. TPEx stock (e.g. 6488)
        v6488 = self.service.get_valuation("6488")
        self.assertIsNotNone(v6488)
        self.assertEqual(v6488["pe"], 42.72)
        self.assertEqual(v6488["pb"], 4.35)
        self.assertEqual(v6488["yield"], 0.88)
        self.assertEqual(v6488["source"], "tpex")

        # 4. Unknown stock
        v9999 = self.service.get_valuation("9999")
        self.assertIsNone(v9999)

    def test_factory_enriches_valuation_when_empty(self):
        mock_val_service = Mock()
        mock_val_service.get_valuation.return_value = {
            "pe": 28.05, "pb": 9.76, "yield": 0.91, "is_loss": False
        }
        mock_twse = Mock()
        mock_twse.fetch_stock_data.return_value = {
            "ticker": "2330",
            "source": "twse",
            "basic_info": {"name": "台積電", "pe": 0.0, "pb": 0.0, "yield": 0.0},
            "financials": {"income_statement": {"revenue": 1000}},
        }
        factory = DataProviderFactory(twse_adapter=mock_twse, valuation_service=mock_val_service)

        data = factory.fetch_all_data("2330")
        self.assertEqual(data["basic_info"]["pb"], 9.76)
        self.assertEqual(data["basic_info"]["yield"], 0.91)
        self.assertEqual(data["basic_info"]["pe"], 28.05)

    def test_report_engine_financials_fallback(self):
        state = StateManager(data_dir=tempfile.mkdtemp())
        state.save_stock_data("2330", {
            "ticker": "2330",
            "basic_info": {"name": "台積電", "pe": 0.0, "pb": 0.0, "yield": 0.0},
            "financials": {
                "income_statement": {"net_income": 1000},
                "balance_sheet": {"total_equity": 5000},
            },
        })
        with patch("src.providers.valuation_service.get_valuation_service") as mock_get_svc:
            mock_svc = Mock()
            mock_svc.get_valuation.return_value = {
                "pe": 28.05, "pb": 9.76, "yield": 0.91, "is_loss": False
            }
            mock_get_svc.return_value = mock_svc

            fin = get_financials("2330", state_manager=state)
            self.assertEqual(fin["pb"], 9.8)
            self.assertEqual(fin["pe"], 28.1)
            self.assertEqual(fin["yield"], 0.9)
            self.assertEqual(fin["roe"], 20.0)

    def test_ai_snapshot_handles_valuation_and_loss(self):
        temp_dir = tempfile.mkdtemp()
        state = StateManager(data_dir=temp_dir)
        # 1. Profitable company with valuation
        state.save_stock_data("2330", {
            "ticker": "2330",
            "price_data_as_of": "2026-10-21",
            "financial_data_updated_at": "2026-10-21",
            "basic_info": {"name": "台積電", "pe": 28.05, "pb": 9.76, "yield": 0.91},
            "financials": {
                "income_statement": {"net_income": 1000},
                "balance_sheet": {"total_equity": 5000},
            },
            "prices": [{"date": "2026-10-21", "close": 1050}],
        })
        snap1 = _stock_snapshot("2330", state)
        self.assertNotIn("股價淨值比", snap1["missing_data"])
        self.assertNotIn("本益比", snap1["missing_data"])
        self.assertNotIn("股利殖利率", snap1["missing_data"])

        # 2. Loss company with PB > 0, PE == 0
        state.save_stock_data("1101", {
            "ticker": "1101",
            "price_data_as_of": "2026-10-21",
            "financial_data_updated_at": "2026-10-21",
            "basic_info": {"name": "台泥", "pe": 0.0, "pb": 0.87, "yield": 2.99, "is_loss": True},
            "financials": {
                "income_statement": {"net_income": -100},
                "balance_sheet": {"total_equity": 5000},
            },
            "prices": [{"date": "2026-10-21", "close": 26.8}],
        })
        snap2 = _stock_snapshot("1101", state)
        self.assertNotIn("本益比", snap2["missing_data"])
        self.assertIn("虧損或無本益比狀態", snap2["basic_info"]["pe_note"])

        # 3. Company missing valuation
        state.save_stock_data("9999", {
            "ticker": "9999",
            "price_data_as_of": "2026-10-21",
            "financial_data_updated_at": "2026-10-21",
            "basic_info": {"name": "未知", "pe": 0.0, "pb": 0.0, "yield": 0.0},
            "financials": {
                "income_statement": {"net_income": 100},
                "balance_sheet": {"total_equity": 5000},
            },
            "prices": [{"date": "2026-10-21", "close": 50}],
        })
        snap3 = _stock_snapshot("9999", state)
        self.assertIn("股價淨值比", snap3["missing_data"])
        self.assertIn("本益比", snap3["missing_data"])
        self.assertIn("股利殖利率", snap3["missing_data"])


if __name__ == "__main__":
    unittest.main()

