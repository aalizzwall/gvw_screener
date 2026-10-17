import sys
import unittest
from datetime import datetime
from types import ModuleType
from unittest.mock import patch

from src.core.orchestrator import Orchestrator
from src.providers.factory import DataProviderFactory
from src.providers.twse_adapter import TWSEAdapter
from src.providers.yahoo_adapter import YahooAdapter


class FakeState:
    def __init__(self, stock_data=None, ranks=None):
        self.stock_data = dict(stock_data or {})
        self.ranks = dict(ranks or {})
        self.health = {}

    def get_stock_data(self, ticker):
        return dict(self.stock_data)

    def save_stock_data(self, ticker, data):
        self.stock_data = dict(data)

    def get_health_status(self):
        return self.health

    def update_stock_health(self, ticker, status):
        self.health[ticker] = status

    def get_ranks(self):
        return self.ranks


class FakeFactory:
    def __init__(self, stock_data, prices):
        self.stock_data = stock_data
        self.prices = prices
        self.stock_calls = 0
        self.price_calls = 0

    def fetch_all_data(self, ticker):
        self.stock_calls += 1
        return self.stock_data

    def fetch_price_history(self, ticker, days=365):
        self.price_calls += 1
        return self.prices


class ProviderResilienceTests(unittest.TestCase):
    def test_twse_selects_matching_stock_from_openapi_lists(self):
        responses = {
            "financials_pl": [
                {"公司代號": "2317", "營業收入": 1},
                {
                    "公司代號": "2330",
                    "營業收入": "1,000,000",
                    "本期淨利（淨損）": "250,000",
                    "營業毛利（毛損）淨額": "500,000",
                    "營業利益（損失）": "300,000",
                    "基本每股盈餘（元）": "12.5",
                },
            ],
            "financials_bs": [{
                "公司代號": "2330",
                "資產總計": "3,000,000",
                "負債總計": "1,000,000",
                "權益總計": "2,000,000",
            }],
            "basic_info": [{"公司代號": "2330", "公司名稱": "台積電"}],
            "shareholders": [{"公司代號": "2330", "股東名稱": "投資人"}],
        }
        adapter = TWSEAdapter()
        with patch.object(adapter, "_get_data", side_effect=lambda key, params=None: responses[key]):
            result = adapter.fetch_stock_data("2330")

        self.assertEqual(result["source"], "twse")
        self.assertEqual(result["financials"]["income_statement"]["revenue"], 1000000)
        self.assertEqual(result["financials"]["income_statement"]["net_income"], 250000)
        self.assertEqual(result["financials"]["income_statement"]["eps"], 12.5)
        self.assertEqual(result["financials"]["balance_sheet"]["total_equity"], 2000000)
        self.assertEqual(result["basic_info"]["公司名稱"], "台積電")

    def test_twse_returns_error_when_no_record_matches(self):
        adapter = TWSEAdapter()
        with patch.object(adapter, "_get_data", return_value=[{"公司代號": "2317"}]):
            result = adapter.fetch_stock_data("2330")

        self.assertIn("error", result)

    def test_factory_falls_through_empty_payloads_to_yahoo(self):
        twse = unittest.mock.Mock()
        finmind = unittest.mock.Mock()
        yahoo = unittest.mock.Mock()
        twse.fetch_stock_data.return_value = {
            "ticker": "2330",
            "financials": {"income_statement": {"revenue": 0}, "balance_sheet": {}},
            "basic_info": {"公司代號": "2330", "產業別": "24"},
        }
        finmind.fetch_stock_data.return_value = {
            "ticker": "2330",
            "financials": {"income_statement": {}, "balance_sheet": {}},
            "basic_info": {"industry": "半導體"},
        }
        yahoo.fetch_stock_data.return_value = {
            "ticker": "2330",
            "source": "yahoo_finance",
            "financials": {"income_statement": {"revenue": 10}},
            "basic_info": {},
        }
        factory = DataProviderFactory(twse, finmind, yahoo)

        result = factory.fetch_all_data("2330")

        self.assertEqual(result["source"], "yahoo_finance")
        twse.fetch_stock_data.assert_called_once_with("2330")
        finmind.fetch_stock_data.assert_called_once_with("2330")
        yahoo.fetch_stock_data.assert_called_once_with("2330")

    def test_factory_uses_yahoo_when_finmind_price_rows_are_invalid(self):
        twse = unittest.mock.Mock()
        finmind = unittest.mock.Mock()
        yahoo = unittest.mock.Mock()
        finmind.fetch_price_history.return_value = [{"date": "2026-10-16", "close": 0}]
        yahoo.fetch_price_history.return_value = [
            {"date": "2026-10-16", "close": 2420, "source": "yahoo_finance"}
        ]
        factory = DataProviderFactory(twse, finmind, yahoo)

        result = factory.fetch_price_history("2330", days=30)

        self.assertEqual(result[0]["close"], 2420)
        self.assertEqual(result[0]["source"], "yahoo_finance")

    def test_yahoo_adapter_returns_real_normalized_market_data(self):
        class FakeHistory:
            empty = False

            def iterrows(self):
                return iter([
                    (datetime(2026, 10, 16), {
                        "Open": 2400, "High": 2450, "Low": 2390, "Close": 2420, "Volume": 100
                    })
                ])

        class FakeTicker:
            info = {
                "longName": "Taiwan Semiconductor Manufacturing Company",
                "totalRevenue": 1000,
                "trailingEps": 10,
                "dividendYield": 0.02,
            }

            def history(self, **kwargs):
                return FakeHistory()

        fake_yfinance = ModuleType("yfinance")
        fake_yfinance.Ticker = lambda symbol: FakeTicker()
        with patch.dict(sys.modules, {"yfinance": fake_yfinance}):
            adapter = YahooAdapter()
            prices = adapter.fetch_price_history("2330", days=30)
            fundamentals = adapter.fetch_stock_data("2330")

        self.assertEqual(prices[-1]["date"], "2026-10-16")
        self.assertEqual(prices[-1]["close"], 2420)
        self.assertEqual(prices[-1]["source"], "yahoo_finance")
        self.assertEqual(fundamentals["financials"]["income_statement"]["revenue"], 1000)
        self.assertEqual(fundamentals["source"], "yahoo_finance")

    def test_orchestrator_refreshes_and_persists_price_data(self):
        state = FakeState({
            "ticker": "2330",
            "updated_at": "2020-01-01T00:00:00",
            "prices": [{"date": "2020-01-01", "close": 1}],
        })
        factory = FakeFactory(
            {"source": "finmind", "financials": {"income_statement": {}, "balance_sheet": {}}, "basic_info": {}},
            [{"date": "2026-10-16", "close": 2420, "source": "finmind"}],
        )
        orchestrator = Orchestrator(state_manager=state, provider_factory=factory)

        result = orchestrator.update_stock("2330", force=True)

        self.assertEqual(result, "SUCCESS")
        self.assertEqual(factory.price_calls, 1)
        self.assertEqual(state.stock_data["price_data_as_of"], "2026-10-16")
        self.assertEqual(state.stock_data["price_data_source"], "finmind")
        self.assertEqual(state.stock_data["prices"][0]["close"], 2420)

    def test_price_ttl_refreshes_without_fetching_current_fundamentals(self):
        state = FakeState({
            "ticker": "2330",
            "updated_at": datetime.now().isoformat(),
            "financial_data_updated_at": datetime.now().isoformat(),
            "price_data_updated_at": "2020-01-01T00:00:00",
        })
        factory = FakeFactory(
            None,
            [{"date": "2026-10-16", "close": 2420, "source": "finmind"}],
        )
        orchestrator = Orchestrator(state_manager=state, provider_factory=factory)

        result = orchestrator.update_stock("2330")

        self.assertEqual(result, "SUCCESS")
        self.assertEqual(factory.stock_calls, 0)
        self.assertEqual(factory.price_calls, 1)
        self.assertEqual(state.stock_data["price_data_as_of"], "2026-10-16")

    def test_failed_refresh_preserves_old_prices_and_data_timestamp(self):
        old_updated_at = "2020-01-01T00:00:00"
        old_prices = [{"date": "2020-01-01", "close": 1}]
        state = FakeState({"ticker": "2330", "updated_at": old_updated_at, "prices": old_prices})
        factory = FakeFactory(None, [])
        orchestrator = Orchestrator(state_manager=state, provider_factory=factory)

        result = orchestrator.update_stock("2330", force=True)

        self.assertEqual(result, "FAIL")
        self.assertEqual(state.stock_data["updated_at"], old_updated_at)
        self.assertEqual(state.stock_data["prices"], old_prices)
        self.assertEqual(state.stock_data["last_refresh_status"], "failed")
        self.assertEqual(state.health["2330"]["status"], "degraded")
        self.assertTrue(state.health["2330"]["retry_after"])

    def test_legacy_dead_status_is_retried_instead_of_permanently_skipped(self):
        state = FakeState()
        state.health["2330"] = {"status": "dead", "failures": 5}
        factory = FakeFactory(
            {"source": "finmind", "financials": {"income_statement": {"revenue": 1}}, "basic_info": {}},
            [{"date": "2026-10-16", "close": 2420, "source": "finmind"}],
        )
        orchestrator = Orchestrator(state_manager=state, provider_factory=factory)

        result = orchestrator.update_stock("2330", force=True)

        self.assertEqual(result, "SUCCESS")
        self.assertEqual(state.health["2330"]["status"], "healthy")
        self.assertIsNone(state.health["2330"]["retry_after"])

    def test_report_shows_actual_price_cutoff_not_report_time(self):
        from src.reporting.report_engine import generate_daily_report

        state = FakeState(ranks={
            "data": [
                {"ticker": "2330", "price_data_as_of": "2026-10-16", "entry_signal": "none"},
                {"ticker": "2317", "price_data_as_of": "2026-10-15", "entry_signal": "none"},
            ]
        })
        with patch("src.reporting.report_engine.get_strong_buy_signals", return_value=[]):
            report = generate_daily_report(state_manager=state)

        self.assertIn("資料截止至</b>: <code>2026-10-15</code>", report)
        self.assertNotIn("系統最後同步時間", report)

    def test_report_shows_unknown_if_any_source_date_is_missing(self):
        from src.reporting.report_engine import generate_daily_report

        state = FakeState(ranks={
            "data": [
                {"ticker": "2330", "price_data_as_of": "2026-10-16"},
                {"ticker": "2317", "price_data_as_of": None},
            ]
        })
        with patch("src.reporting.report_engine.get_strong_buy_signals", return_value=[]):
            report = generate_daily_report(state_manager=state)

        self.assertIn("資料截止至</b>: <code>未知</code>", report)


if __name__ == "__main__":
    unittest.main()
