import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from src.analysis.ai_analyst import analyze_stock
from src.state.state_manager import StateManager


class TestAIAnalyst(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state = StateManager(
            data_dir=f"{self.temp_dir.name}/data",
            health_file=f"{self.temp_dir.name}/health.json",
            universe_file=f"{self.temp_dir.name}/universe.json",
            result_file=f"{self.temp_dir.name}/ranks.json",
            market_index_file=f"{self.temp_dir.name}/market.json",
        )
        self.state.save_stock_data("2330", {
            "ticker": "2330",
            "name": "台積電",
            "prices": [
                {"date": "2026-10-20", "close": 1800, "volume": 1234},
            ],
            "price_data_as_of": "2026-10-20",
            "price_data_updated_at": "2026-10-21T10:00:00",
            "price_data_source": "finmind",
            "financial_data_updated_at": "2026-10-20T15:00:00",
            "financial_data_source": "finmind",
            "basic_info": {"industry": "半導體", "pe": 20, "pb": 5},
            "financials": {
                "income_statement": {"revenue": 1000, "net_income": 200},
                "balance_sheet": {"total_equity": 500},
            },
        })
        self.state.save_ranks({"data": [{
            "ticker": "2330",
            "final_score": 0.91,
            "entry_signal": "回調觀察",
            "t_score": 0.8,
        }]})

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_analysis_uses_saved_taiwan_stock_data_and_returns_model_report(self):
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": "看多理由：營收穩健。反方風險：估值偏高。"}}]
        }

        with patch("src.analysis.ai_analyst.requests.post", return_value=response) as post:
            report = analyze_stock("2330", self.state, api_key="test-key")

        self.assertEqual(report, "看多理由：營收穩健。反方風險：估值偏高。")
        request = post.call_args.kwargs
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-key")
        payload = request["json"]
        self.assertEqual(payload["model"], "gpt-4o-mini")
        context = payload["messages"][1]["content"]
        self.assertIn("台積電", context)
        self.assertIn("2026-10-20", context)
        self.assertIn("半導體", context)
        self.assertNotIn("test-key", context)

    def test_analysis_rejects_stock_without_saved_data(self):
        with patch("src.analysis.ai_analyst.requests.post") as post:
            with self.assertRaisesRegex(ValueError, "尚無已採集資料"):
                analyze_stock("9999", self.state, api_key="test-key")

        post.assert_not_called()

    def test_analysis_context_identifies_missing_valuation_data(self):
        stock = self.state.get_stock_data("2330")
        stock["basic_info"] = {"industry": "半導體", "pe": 0, "pb": 0, "yield": 0}
        stock["financials"] = {"income_statement": {}, "balance_sheet": {}}
        self.state.save_stock_data("2330", stock)
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": "資料不足，暫不評論。"}}]
        }

        with patch("src.analysis.ai_analyst.requests.post", return_value=response) as post:
            analyze_stock("2330", self.state, api_key="test-key")

        context = post.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertIn("本益比", context)
        self.assertIn("股價淨值比", context)
        self.assertIn("股利殖利率", context)

    def test_analysis_reports_missing_api_key_before_calling_provider(self):
        with patch("src.analysis.ai_analyst.requests.post") as post:
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                analyze_stock("2330", self.state, api_key="")

        post.assert_not_called()

    def test_analysis_surfaces_provider_failures_without_fabricated_result(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("provider unavailable")

        with patch("src.analysis.ai_analyst.requests.post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "OpenAI 分析請求失敗"):
                analyze_stock("2330", self.state, api_key="test-key")

    def test_analysis_logs_provider_status_and_safe_error_details(self):
        response = Mock()
        response.status_code = 401
        response.json.return_value = {
            "error": {
                "type": "authentication_error",
                "code": "invalid_api_key",
                "message": "Incorrect API key provided: test-secret",
            }
        }
        response.raise_for_status.side_effect = requests.HTTPError(
            "401 Client Error", response=response
        )

        with (
            patch("src.analysis.ai_analyst.requests.post", return_value=response),
            self.assertLogs("src.analysis.ai_analyst", level="ERROR") as logs,
            self.assertRaisesRegex(RuntimeError, "OpenAI 分析請求失敗"),
        ):
            analyze_stock("2330", self.state, api_key="test-secret")

        log_output = "\n".join(logs.output)
        self.assertIn("401", log_output)
        self.assertIn("invalid_api_key", log_output)
        self.assertIn("Incorrect API key provided:", log_output)
        self.assertNotIn("test-secret", log_output)


if __name__ == "__main__":
    unittest.main()
