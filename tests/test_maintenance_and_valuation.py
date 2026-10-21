import os
import sys
import json
import time
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.agent.maintenance_agent import MaintenanceAgent
from src.providers.twse_adapter import TWSEAdapter
from gvw_bot.bot_handler import TelegramBotHandler
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

            mock_ask.assert_called_once_with("為什麼 PB PE 是空白的？")
            calls = [c.args[1] for c in self.bot.send_message.call_args_list]
            self.assertTrue(any("分析您的問題" in c for c in calls))
            self.assertTrue(any("經診斷，系統正常。" in c for c in calls))

    def test_maintenance_mode_exit_keyword(self):
        self.bot.maintenance_mode_chats.add(10)
        self.bot.handle_command(10, "exit", "exit", user_id=10)
        self.assertNotIn(10, self.bot.maintenance_mode_chats)
        calls = [c.args[1] for c in self.bot.send_message.call_args_list]
        self.assertTrue(any("已退出【系統維護模式】" in c for c in calls))


if __name__ == "__main__":
    unittest.main()

