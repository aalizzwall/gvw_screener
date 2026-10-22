import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from src.analysis.ai_analyst import analyze_stock, check_providers, _request_completion
from src.analysis.model_prober import _PROBE_CACHE
from src.state.ai_provider import AIProviderPreferenceStore
from src.state.state_manager import StateManager


class TestAIAnalyst(unittest.TestCase):

    def setUp(self):
        _PROBE_CACHE.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.provider_store = AIProviderPreferenceStore(f"{self.temp_dir.name}/ai_provider.json")
        for target, value in (
            ("src.analysis.ai_analyst.GEMINI_API_KEY", ""),
            ("src.analysis.ai_analyst.OPENAI_API_KEY", ""),
            ("src.analysis.ai_analyst.LOCAL_AI_BASE_URL", ""),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(
            "src.analysis.ai_analyst.AIProviderPreferenceStore",
            return_value=self.provider_store,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
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
        _PROBE_CACHE.clear()
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

    @staticmethod
    def _ok_response(text):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": text}}]}
        return response

    @staticmethod
    def _http_error(status_code):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = {"error": {"message": "slow down", "code": "rate_limit"}}
        response.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} error", response=response
        )
        return response

    def test_falls_back_to_gemma_when_openai_is_rate_limited(self):
        responses = [self._http_error(429), self._ok_response("Gemma 分析結果")]

        with (
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", "gem-key"),
            patch("src.analysis.ai_analyst.requests.post", side_effect=responses) as post,
        ):
            report = analyze_stock("2330", self.state, api_key="test-key")

        self.assertEqual(report, "Gemma 分析結果")
        self.assertEqual(post.call_count, 2)
        first, second = post.call_args_list
        self.assertIn("api.openai.com", first.args[0])
        self.assertIn("generativelanguage.googleapis.com", second.args[0])
        self.assertEqual(second.kwargs["headers"]["Authorization"], "Bearer gem-key")
        self.assertEqual(second.kwargs["json"]["model"], "gemma-3-27b-it")
        # Gemma rejects system instructions, so they are merged into one user message.
        messages = second.kwargs["json"]["messages"]
        self.assertEqual([m["role"] for m in messages], ["user"])
        self.assertIn("台積電", messages[0]["content"])
        self.assertIn("台灣股票研究助理", messages[0]["content"])

    def test_owner_selected_chain_order_and_models_are_used(self):
        self.provider_store.set_chain(
            ["gemma/gemma-3-12b-it", "openai/gpt-4o", "openai/gpt-4o-mini"]
        )

        with (
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", "gem-key"),
            patch(
                "src.analysis.ai_analyst.requests.post",
                return_value=self._ok_response("Gemma 先答"),
            ) as post,
        ):
            report = analyze_stock("2330", self.state, api_key="test-key")

        self.assertEqual(report, "Gemma 先答")
        post.assert_called_once()
        self.assertIn("generativelanguage.googleapis.com", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gemma-3-12b-it")

    def test_falls_through_models_of_same_provider_before_next_provider(self):
        self.provider_store.set_chain(
            ["openai/gpt-4o", "openai/gpt-4o-mini", "gemma/gemma-3-27b-it"]
        )
        responses = [
            self._http_error(429),
            self._http_error(429),
            self._ok_response("第三個才成功"),
        ]

        with (
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", "gem-key"),
            patch("src.analysis.ai_analyst.requests.post", side_effect=responses) as post,
        ):
            report = analyze_stock("2330", self.state, api_key="test-key")

        self.assertEqual(report, "第三個才成功")
        self.assertEqual(
            [call.kwargs["json"]["model"] for call in post.call_args_list],
            ["gpt-4o", "gpt-4o-mini", "gemma-3-27b-it"],
        )

    def test_skips_provider_without_key_and_reports_all_failures(self):
        with (
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", "gem-key"),
            patch(
                "src.analysis.ai_analyst.requests.post",
                side_effect=[self._http_error(429), self._http_error(503)],
            ),
            self.assertRaisesRegex(RuntimeError, "OpenAI、Google Gemma 分析請求失敗"),
        ):
            analyze_stock("2330", self.state, api_key="test-key")

    def test_uses_only_gemma_when_openai_key_missing(self):
        with (
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", "gem-key"),
            patch(
                "src.analysis.ai_analyst.requests.post",
                return_value=self._ok_response("只有 Gemma"),
            ) as post,
        ):
            report = analyze_stock("2330", self.state, api_key="")

        self.assertEqual(report, "只有 Gemma")
        post.assert_called_once()

    def test_falls_back_to_local_when_cloud_providers_fail(self):
        responses = [
            self._http_error(429),  # OpenAI fails
            self._http_error(503),  # Gemma fails
            self._ok_response("地端 Qwen 3.6 9B 保底成功"),  # Local succeeds
        ]

        with (
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", "gem-key"),
            patch("src.analysis.ai_analyst.LOCAL_AI_BASE_URL", "http://192.168.1.50:1234/v1"),
            patch("src.analysis.ai_analyst.requests.post", side_effect=responses) as post,
        ):
            report = analyze_stock("2330", self.state, api_key="test-key")

        self.assertEqual(report, "地端 Qwen 3.6 9B 保底成功")
        self.assertEqual(post.call_count, 3)
        third_call = post.call_args_list[2]
        self.assertIn("192.168.1.50:1234/v1/chat/completions", third_call.args[0])
        self.assertEqual(third_call.kwargs["json"]["model"], "active")

    def test_local_ai_detects_active_model_from_lm_studio(self):
        self.provider_store.set_chain(["local/active"])
        models_resp = Mock(status_code=200)
        models_resp.json.return_value = {"data": [{"id": "qwen-3.6-9b-instruct"}]}

        with (
            patch("src.analysis.ai_analyst.LOCAL_AI_BASE_URL", "http://localhost:1234/v1"),
            patch("src.analysis.ai_analyst.requests.get", return_value=models_resp) as get,
            patch(
                "src.analysis.ai_analyst.requests.post",
                return_value=self._ok_response("Qwen 產出分析"),
            ) as post,
        ):
            report = analyze_stock("2330", self.state)

        self.assertEqual(report, "Qwen 產出分析")
        get.assert_called_once()
        self.assertIn("http://localhost:1234/v1/models", get.call_args.args[0])
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"]["model"], "qwen-3.6-9b-instruct")

    def test_check_providers_reports_each_provider_health(self):
        with (
            patch("src.analysis.ai_analyst.OPENAI_API_KEY", "oa-key"),
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", ""),
            patch("src.analysis.ai_analyst.LOCAL_AI_BASE_URL", ""),
            patch(
                "src.analysis.ai_analyst.requests.post",
                return_value=self._http_error(429),
            ),
        ):
            results = check_providers()

        self.assertEqual(
            [r["entry"] for r in results],
            ["openai/gpt-4o-mini", "gemma/gemma-3-27b-it", "local/active"],
        )
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["detail"], "HTTP 429")
        self.assertFalse(results[1]["ok"])
        self.assertIn("GEMINI_API_KEY", results[1]["detail"])
        self.assertFalse(results[2]["ok"])
        self.assertIn("LOCAL_AI_BASE_URL", results[2]["detail"])

    @patch("src.analysis.model_prober.requests.get")
    def test_two_tier_fallback_intra_provider_before_inter_provider(self, mock_probe_get):
        self.provider_store.set_chain(["openai/gpt-4o", "local/active"])
        probe_resp = Mock(status_code=200)
        probe_resp.json.return_value = {
            "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]
        }
        mock_probe_get.return_value = probe_resp

        responses = [
            self._http_error(429),
            self._ok_response("gpt-4o-mini intra-provider fallback 成功"),
        ]
        with (
            patch("src.analysis.ai_analyst.LOCAL_AI_BASE_URL", "http://localhost:1234/v1"),
            patch("src.analysis.ai_analyst.requests.post", side_effect=responses) as post,
        ):
            report = analyze_stock("2330", self.state, api_key="test-key")

        self.assertEqual(report, "gpt-4o-mini intra-provider fallback 成功")
        self.assertEqual(post.call_count, 2)
        self.assertIn("api.openai.com", post.call_args_list[0].args[0])
        self.assertEqual(post.call_args_list[0].kwargs["json"]["model"], "gpt-4o")
        self.assertIn("api.openai.com", post.call_args_list[1].args[0])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "gpt-4o-mini")

    @patch("src.analysis.model_prober.requests.get")
    def test_two_tier_fallback_401_skips_intra_provider_and_cascades(self, mock_probe_get):
        self.provider_store.set_chain(["openai/gpt-4o", "gemma/gemini-1.5-flash"])
        probe_resp = Mock(status_code=200)
        probe_resp.json.return_value = {
            "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}, {"id": "gemini-1.5-flash"}]
        }
        mock_probe_get.return_value = probe_resp

        err_401 = requests.Response()
        err_401.status_code = 401
        err_401._content = b'{"error": {"message": "Invalid API Key"}}'

        responses = [
            err_401,
            self._ok_response("Gemma 跨 Provider 接手成功"),
        ]
        with (
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", "gem-key"),
            patch("src.analysis.ai_analyst.requests.post", side_effect=responses) as post,
        ):
            report = analyze_stock("2330", self.state, api_key="test-key")

        self.assertEqual(report, "Gemma 跨 Provider 接手成功")
        self.assertEqual(post.call_count, 2)
        self.assertIn("api.openai.com", post.call_args_list[0].args[0])
        self.assertIn("generativelanguage.googleapis.com", post.call_args_list[1].args[0])

    @patch("src.analysis.model_prober.requests.get")
    def test_dynamic_probe_auto_corrects_invalid_gemma_guess(self, mock_probe_get):
        self.provider_store.set_chain(["gemma/gemma-3-27b-it"])
        probe_resp = Mock(status_code=200)
        probe_resp.json.return_value = {
            "data": [{"id": "gemini-1.5-flash"}, {"id": "gemini-2.0-flash"}]
        }
        mock_probe_get.return_value = probe_resp

        with (
            patch("src.analysis.ai_analyst.GEMINI_API_KEY", "gem-key"),
            patch(
                "src.analysis.ai_analyst.requests.post",
                return_value=self._ok_response("修正模型成功"),
            ) as post,
        ):
            report = analyze_stock("2330", self.state)

        self.assertEqual(report, "修正模型成功")
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gemini-1.5-flash")

    def test_request_completion_supports_reasoning_models_like_deepseek(self):
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "<think>\nThinking...\n</think>\n分析：建議回調觀察",
                    }
                }
            ]
        }
        with patch("src.analysis.ai_analyst.requests.post", return_value=resp):
            text = _request_completion(
                "local", "", "deepseek-r1", [{"role": "user", "content": "ping"}], 32
            )
        self.assertIn("分析：建議回調觀察", text)


class TestAIProviderPreferenceStore(unittest.TestCase):

    def test_defaults_then_persists_owner_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AIProviderPreferenceStore(f"{tmp}/ai_provider.json")
            self.assertEqual(
                store.get_chain(),
                ["openai/gpt-4o-mini", "gemma/gemma-3-27b-it", "local/active"],
            )

            store.set_chain(["gemma/gemma-3-27b-it", "openai/gpt-4o", "gemma/gemma-3-27b-it"])
            reloaded = AIProviderPreferenceStore(store.path)
            self.assertEqual(reloaded.get_chain(), ["gemma/gemma-3-27b-it", "openai/gpt-4o"])

    def test_local_base_url_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AIProviderPreferenceStore(f"{tmp}/ai_provider.json")
            self.assertEqual(store.get_local_base_url(), "")

            updated = store.set_local_base_url("http://192.168.1.88:1234/v1")
            self.assertEqual(updated, "http://192.168.1.88:1234/v1")

            reloaded = AIProviderPreferenceStore(store.path)
            self.assertEqual(reloaded.get_local_base_url(), "http://192.168.1.88:1234/v1")

            with self.assertRaises(ValueError):
                store.set_local_base_url("invalid-url")

    def test_add_remove_and_whitelist_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AIProviderPreferenceStore(f"{tmp}/ai_provider.json")
            store.set_chain(["openai/gpt-4o"])

            self.assertEqual(
                store.add("gemma/gemma-3-12b-it", position=1),
                ["gemma/gemma-3-12b-it", "openai/gpt-4o"],
            )
            self.assertEqual(store.remove("1"), ["openai/gpt-4o"])
            with self.assertRaises(ValueError):
                store.remove("openai/gpt-4o")  # last entry cannot be removed
            for bad in ("openai/gpt-9", "foo/bar", "gemma", ""):
                with self.assertRaises(ValueError):
                    store.add(bad)

    def test_reads_legacy_primary_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/ai_provider.json"
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"primary": "gemma"}')
            self.assertEqual(
                AIProviderPreferenceStore(path).get_chain(),
                ["gemma/gemma-3-27b-it", "openai/gpt-4o-mini", "local/active"],
            )


if __name__ == "__main__":
    unittest.main()
