import unittest
from unittest.mock import Mock, patch

from src.analysis.model_prober import (
    _PROBE_CACHE,
    _filter_and_rank_models,
    get_intra_provider_pool,
    probe_available_models,
)


class TestModelProber(unittest.TestCase):
    def setUp(self):
        _PROBE_CACHE.clear()

    def test_filter_and_rank_models_openai(self):
        discovered = [
            "text-embedding-3-small",
            "whisper-1",
            "gpt-4o",
            "gpt-3.5-turbo",
            "dall-e-3",
            "gpt-4o-mini",
            "gpt-4-custom-fine-tune",
        ]
        ranked = _filter_and_rank_models("openai", discovered)
        # Should prioritize gpt-4o-mini, gpt-4o, gpt-3.5-turbo according to preferences
        self.assertEqual(ranked[0], "gpt-4o-mini")
        self.assertEqual(ranked[1], "gpt-4o")
        self.assertEqual(ranked[2], "gpt-3.5-turbo")
        self.assertIn("gpt-4-custom-fine-tune", ranked)
        # Non-chat models filtered out
        self.assertNotIn("text-embedding-3-small", ranked)
        self.assertNotIn("whisper-1", ranked)
        self.assertNotIn("dall-e-3", ranked)

    def test_filter_and_rank_models_gemma(self):
        discovered = [
            "text-embedding-004",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemma-2-9b-it",
            "aqa",
        ]
        ranked = _filter_and_rank_models("gemma", discovered)
        self.assertEqual(ranked[0], "gemini-1.5-flash")
        self.assertEqual(ranked[1], "gemini-1.5-pro")
        self.assertEqual(ranked[2], "gemma-2-9b-it")
        self.assertNotIn("text-embedding-004", ranked)
        self.assertNotIn("aqa", ranked)

    @patch("src.analysis.model_prober.requests.get")
    def test_probe_openai_success(self, mock_get):
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": [
                {"id": "gpt-4o"},
                {"id": "text-embedding-3-large"},
                {"id": "gpt-4o-mini"},
            ]
        }
        mock_get.return_value = resp

        ok, models, detail = probe_available_models("openai", "sk-test", force_refresh=True)
        self.assertTrue(ok)
        self.assertEqual(models, ["gpt-4o-mini", "gpt-4o"])
        self.assertIn("探測成功", detail)

    @patch("src.analysis.model_prober.requests.get")
    def test_probe_openai_401(self, mock_get):
        resp = Mock(status_code=401)
        mock_get.return_value = resp

        ok, models, detail = probe_available_models("openai", "sk-bad", force_refresh=True)
        self.assertFalse(ok)
        self.assertEqual(models, [])
        self.assertIn("401", detail)

    @patch("src.analysis.model_prober.requests.get")
    def test_probe_gemma_openai_endpoint_success(self, mock_get):
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": [
                {"id": "models/gemini-1.5-flash"},
                {"id": "models/gemini-1.5-pro"},
            ]
        }
        mock_get.return_value = resp

        ok, models, detail = probe_available_models("gemma", "gm-test", force_refresh=True)
        self.assertTrue(ok)
        self.assertEqual(models, ["gemini-1.5-flash", "gemini-1.5-pro"])

    @patch("src.analysis.model_prober.requests.get")
    def test_probe_gemma_filters_out_antigravity_preview_and_strips_models_prefix(self, mock_get):
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": [
                {"id": "models/antigravity-preview-05-2026"},
                {"id": "models/antigravity-preview-latest"},
                {"id": "models/gemini-1.5-flash"},
                {"id": "models/gemini-2.0-flash"},
                {"id": "models/gemini-1.5-pro"},
                {"id": "models/text-embedding-004"},
            ]
        }
        mock_get.return_value = resp

        ok, models, detail = probe_available_models("gemma", "gm-test", force_refresh=True)
        self.assertTrue(ok)
        self.assertNotIn("antigravity-preview-05-2026", models)
        self.assertNotIn("models/antigravity-preview-05-2026", models)
        self.assertNotIn("text-embedding-004", models)
        self.assertEqual(models, ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"])

    @patch("src.analysis.model_prober.requests.get")
    def test_probe_gemma_fallback_to_native_endpoint(self, mock_get):
        resp1 = Mock(status_code=404)
        resp2 = Mock(status_code=200)
        resp2.json.return_value = {
            "models": [
                {"name": "models/gemini-2.0-flash"},
                {"name": "models/gemini-1.5-flash"},
            ]
        }
        mock_get.side_effect = [resp1, resp2]

        ok, models, detail = probe_available_models("gemma", "gm-test", force_refresh=True)
        self.assertTrue(ok)
        self.assertEqual(models, ["gemini-1.5-flash", "gemini-2.0-flash"])

    @patch("src.analysis.model_prober.requests.get")
    def test_probe_local_success(self, mock_get):
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": [
                {"id": "qwen2.5-7b-instruct"},
            ]
        }
        mock_get.return_value = resp

        ok, models, detail = probe_available_models(
            "local", "", local_base_url="http://localhost:1234/v1", force_refresh=True
        )
        self.assertTrue(ok)
        self.assertEqual(models, ["qwen2.5-7b-instruct"])

    def test_probe_no_key(self):
        ok, models, detail = probe_available_models("openai", "")
        self.assertFalse(ok)
        self.assertIn("未設定 API 金鑰", detail)

    @patch("src.analysis.model_prober.requests.get")
    def test_intra_provider_pool_with_invalid_guess_falls_back_to_probed(self, mock_get):
        # User had configured gemma-3-27b-it (which returned 404),
        # but probe discovers gemini-1.5-flash and gemini-2.0-flash
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": [
                {"id": "gemini-1.5-flash"},
                {"id": "gemini-2.0-flash"},
                {"id": "gemini-1.5-pro"},
            ]
        }
        mock_get.return_value = resp

        pool = get_intra_provider_pool(
            "gemma", "gm-key", preferred_model="gemma-3-27b-it", max_models=2, force_refresh=True
        )
        # Should NOT guess gemma-3-27b-it, but rather use discovered models!
        self.assertEqual(pool, ["gemini-1.5-flash", "gemini-2.0-flash"])

    @patch("src.analysis.model_prober.requests.get")
    def test_intra_provider_pool_retains_valid_preferred_model(self, mock_get):
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": [
                {"id": "gpt-4o-mini"},
                {"id": "gpt-4o"},
                {"id": "gpt-4.1-mini"},
            ]
        }
        mock_get.return_value = resp

        # Preferred model is gpt-4o, which exists in probed models
        pool = get_intra_provider_pool(
            "openai", "sk-key", preferred_model="gpt-4o", max_models=2, force_refresh=True
        )
        self.assertEqual(pool, ["gpt-4o", "gpt-4o-mini"])

    @patch("src.analysis.model_prober.requests.get")
    def test_intra_provider_pool_offline_falls_back_to_preferred(self, mock_get):
        mock_get.side_effect = ConnectionError("No network")

        pool = get_intra_provider_pool(
            "openai", "sk-key", preferred_model="gpt-4o-mini", force_refresh=True
        )
        # When probe fails, do not guess extra models
        self.assertEqual(pool, ["gpt-4o-mini"])


if __name__ == "__main__":
    unittest.main()
