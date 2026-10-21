import sys
import unittest
from datetime import datetime
from types import ModuleType
from unittest.mock import patch

import requests
from src.core.orchestrator import Orchestrator
from src.providers.factory import DataProviderFactory
from src.providers.twse_adapter import TWSEAdapter
from src.providers.yahoo_adapter import TickerNotFoundError, YahooAdapter


class FakeState:
    def __init__(self, stock_data=None, ranks=None, universe=None):
        self.stock_data = dict(stock_data or {})
        self.ranks = dict(ranks or {})
        self.universe = dict(universe or {})
        self.health = {}

    def get_universe(self):
        return dict(self.universe)

    def save_universe(self, universe):
        self.universe = dict(universe)

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

    def fetch_universe(self):
        return {"2330": "台積電"}

    def fetch_all_data(self, ticker):
        self.stock_calls += 1
        return self.stock_data

    def fetch_price_history(self, ticker, days=365):
        self.price_calls += 1
        return self.prices


class ProviderResilienceTests(unittest.TestCase):
    def test_yahoo_tries_otc_symbol_after_twse_symbol_returns_404(self):
        not_found = unittest.mock.Mock(status_code=404)
        chart = {
            "chart": {
                "result": [{
                    "timestamp": [datetime(2026, 10, 16, 12).timestamp()],
                    "indicators": {
                        "quote": [{
                            "open": [100], "high": [105], "low": [99],
                            "close": [103], "volume": [1000],
                        }]
                    },
                }],
                "error": None,
            }
        }
        found = unittest.mock.Mock(status_code=200, json=lambda: chart)
        found.raise_for_status.return_value = None
        adapter = YahooAdapter()

        with patch("src.providers.yahoo_adapter.requests.get", side_effect=[not_found, found]) as get:
            prices = adapter.fetch_price_history("6488", days=30)

        self.assertEqual(len(prices), 1)
        self.assertEqual(prices[0]["close"], 103)
        self.assertIn("6488.TW", get.call_args_list[0].args[0])
        self.assertIn("6488.TWO", get.call_args_list[1].args[0])

    def test_yahoo_reports_missing_only_after_both_market_symbols_return_404(self):
        not_found = unittest.mock.Mock(status_code=404)
        adapter = YahooAdapter()

        with patch(
            "src.providers.yahoo_adapter.requests.get",
            side_effect=[not_found, not_found],
        ):
            with self.assertRaises(TickerNotFoundError):
                adapter.fetch_price_history("6488", days=30)

    def test_factory_preserves_confirmed_yahoo_missing_result(self):
        twse = unittest.mock.Mock()
        finmind = unittest.mock.Mock()
        yahoo = unittest.mock.Mock()
        finmind.fetch_price_history.return_value = []
        yahoo.fetch_price_history.side_effect = TickerNotFoundError("6488")
        factory = DataProviderFactory(twse, finmind, yahoo)

        with self.assertRaises(TickerNotFoundError):
            factory.fetch_price_history("6488", days=30)

    def test_yahoo_transient_failure_does_not_report_missing_ticker(self):
        not_found = unittest.mock.Mock(status_code=404)
        adapter = YahooAdapter()

        with patch(
            "src.providers.yahoo_adapter.requests.get",
            side_effect=[not_found, requests.ConnectionError("temporary network failure")],
        ):
            self.assertEqual(adapter.fetch_price_history("6488", days=30), [])

    def test_persistent_yahoo_404_marks_ticker_dead_and_allows_forced_retry(self):
        class MissingTickerFactory(FakeFactory):
            def fetch_all_data(self, ticker):
                self.stock_calls += 1
                return None

            def fetch_price_history(self, ticker, days=365):
                self.price_calls += 1
                raise TickerNotFoundError(ticker)

        state = FakeState()
        factory = MissingTickerFactory(None, [])
        orchestrator = Orchestrator(state_manager=state, provider_factory=factory)

        for _ in range(3):
            self.assertEqual(
                orchestrator.update_stock("6488", force=True, health_status=state.health),
                "FAIL",
            )

        self.assertEqual(state.health["6488"]["status"], "dead")
        self.assertEqual(state.health["6488"]["yahoo_not_found_count"], 3)
        self.assertEqual(
            orchestrator.update_stock("6488", health_status=state.health),
            "SKIP",
        )
        self.assertEqual(factory.stock_calls, 3)
        self.assertEqual(factory.price_calls, 3)
        self.assertEqual(
            orchestrator.update_stock("6488", force=True, health_status=state.health),
            "FAIL",
        )
        self.assertEqual(state.health["6488"]["yahoo_not_found_count"], 3)
        self.assertEqual(factory.stock_calls, 4)

    def test_partial_financial_success_marks_dead_on_third_yahoo_404(self):
        stock_data = {
            "source": "twse",
            "financials": {"income_statement": {"revenue": 1}, "balance_sheet": {}},
            "basic_info": {},
        }

        class PartiallyAvailableFactory(FakeFactory):
            def __init__(self):
                super().__init__(stock_data, [])
                self.fetch_count = 0

            def fetch_all_data(self, ticker):
                self.stock_calls += 1
                self.fetch_count += 1
                return stock_data

            def fetch_price_history(self, ticker, days=365):
                self.price_calls += 1
                raise TickerNotFoundError(ticker)

        state = FakeState()
        factory = PartiallyAvailableFactory()
        orchestrator = Orchestrator(state_manager=state, provider_factory=factory)

        self.assertEqual(
            orchestrator.update_stock("6488", force=True, health_status=state.health),
            "PARTIAL",
        )
        self.assertEqual(state.health["6488"]["yahoo_not_found_count"], 1)
        self.assertEqual(
            orchestrator.update_stock("6488", force=True, health_status=state.health),
            "PARTIAL",
        )
        self.assertEqual(
            orchestrator.update_stock("6488", force=True, health_status=state.health),
            "PARTIAL",
        )
        self.assertEqual(state.health["6488"]["yahoo_not_found_count"], 3)
        self.assertEqual(state.health["6488"]["status"], "dead")

    def test_force_price_update_continues_after_missing_ticker(self):
        state = unittest.mock.Mock()
        state.get_universe.return_value = {}
        state.list_stock_tickers.return_value = ["6488", "2330"]
        state.get_stock_data.return_value = {}
        factory = unittest.mock.Mock()
        factory.fetch_price_history.side_effect = [
            TickerNotFoundError("6488"),
            [{"date": "2026-10-16", "close": 100}],
        ]
        orchestrator = Orchestrator(state_manager=state, provider_factory=factory)

        updated = orchestrator.force_update_prices()

        self.assertEqual(updated, 1)
        self.assertEqual(factory.fetch_price_history.call_count, 2)

    def test_transient_collection_failures_do_not_mark_ticker_dead(self):
        state = FakeState()
        factory = FakeFactory(None, [])
        orchestrator = Orchestrator(state_manager=state, provider_factory=factory)

        for _ in range(3):
            self.assertEqual(
                orchestrator.update_stock("6488", force=True, health_status=state.health),
                "FAIL",
            )

        self.assertEqual(state.health["6488"]["status"], "degraded")
        self.assertEqual(state.health["6488"]["yahoo_not_found_count"], 0)

    def test_empty_universe_is_initialized_and_persisted(self):
        state = FakeState()
        orchestrator = Orchestrator(state_manager=state, provider_factory=FakeFactory({}, []))

        universe = orchestrator.ensure_universe()

        self.assertEqual(universe, {"2330": "台積電"})
        self.assertEqual(state.universe, universe)

    def test_factory_merges_twse_and_tpex_company_lists(self):
        responses = [
            unittest.mock.Mock(
                json=lambda: [{"公司代號": "2330", "公司名稱": "台積電"}]
            ),
            unittest.mock.Mock(
                json=lambda: [{"公司代號": "6488", "公司名稱": "環球晶"}]
            ),
        ]
        for response in responses:
            response.raise_for_status.return_value = None
        factory = DataProviderFactory()

        with patch("src.providers.factory.requests.get", side_effect=responses) as get:
            universe = factory.fetch_universe()

        self.assertEqual(universe, {"2330": "台積電", "6488": "環球晶"})
        self.assertEqual(get.call_count, 2)

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
        chart = {
            "chart": {
                "result": [{
                    "timestamp": [datetime(2026, 10, 16, 12).timestamp()],
                    "meta": {"gmtoffset": 0},
                    "indicators": {
                        "quote": [{
                            "open": [2400], "high": [2450], "low": [2390],
                            "close": [2420], "volume": [100],
                        }]
                    },
                }],
                "error": None,
            }
        }
        response = unittest.mock.Mock(status_code=200, json=lambda: chart)
        response.raise_for_status.return_value = None
        with patch.dict(sys.modules, {"yfinance": fake_yfinance}), patch(
            "src.providers.yahoo_adapter.requests.get", return_value=response
        ):
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
