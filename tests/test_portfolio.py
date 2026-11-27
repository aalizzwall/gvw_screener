import os
import tempfile
import unittest
from datetime import date, timedelta

from src.state.portfolio_store import PortfolioStore
from src.analysis.portfolio_monitor import check_portfolio, check_dividend_alerts
from src.analysis.trade_geometry import compute_trade_plan


def _bars():
    """Downtrend -> flat platform (~105-109) -> breakout, same shape as test_trade_geometry."""
    bars = []
    for i in range(30):
        bars.append({"date": f"d{i}", "close": 140 - i, "high": 141 - i, "low": 139 - i,
                     "open": 140 - i, "volume": 1000})
    for i in range(40):
        c = 105 + (i % 5)
        bars.append({"date": f"p{i}", "close": c, "high": c + 1, "low": c - 1,
                     "open": c, "volume": 500 if i != 20 else 100})
    for i in range(5):
        bars.append({"date": f"b{i}", "close": 112 + i, "high": 113 + i, "low": 111 + i,
                     "open": 111 + i, "volume": 2000 if i == 0 else 600})
    return bars


def _expected_levels(plan, current):
    return {
        level for level in ("target_1", "target_2", "stop_loss")
        if (current <= plan[level] if level == "stop_loss" else current >= plan[level])
    }


class FakeState:
    def __init__(self, data):
        self.data = data

    def get_stock_data(self, ticker):
        return self.data.get(ticker, {})


class PortfolioStoreTests(unittest.TestCase):
    def test_add_remove_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "user_portfolio.json")
            store = PortfolioStore(path)
            self.assertTrue(store.add("2330", 100.5, "2026-10-31", 42))
            entry = store.list()["2330"]
            self.assertEqual(entry["entry_price"], 100.5)
            self.assertEqual(entry["entry_date"], "2026-10-31")
            self.assertEqual(entry["chat_id"], 42)
            self.assertEqual(entry["alerted"], {"target_1": False, "target_2": False, "stop_loss": False})

            # persistence: a fresh store reads the same file
            reloaded = PortfolioStore(path)
            self.assertIn("2330", reloaded.list())

            self.assertTrue(reloaded.remove("2330"))
            self.assertFalse(reloaded.remove("2330"))
            self.assertEqual(PortfolioStore(path).list(), {})

    def test_corrupt_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "user_portfolio.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json at all")
            store = PortfolioStore(path)
            self.assertEqual(store.list(), {})
            self.assertTrue(store.add("2330", 10, "2026-10-31", 1))
            self.assertIn("2330", PortfolioStore(path).list())


class PortfolioMonitorTests(unittest.TestCase):
    def _store(self, temp_dir):
        store = PortfolioStore(os.path.join(temp_dir, "portfolio.json"))
        store.add("2330", 100.0, "2026-10-31", 42)
        return store

    def test_alerts_exactly_once_per_level(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            bars = _bars()
            plan = compute_trade_plan(bars)
            self.assertIsNotNone(plan)
            current = bars[-1]["close"]
            state = FakeState({"2330": {"prices": bars}})

            alerts = check_portfolio(store, state)
            expected = _expected_levels(plan, current)
            self.assertEqual({a["level"] for a in alerts}, expected)
            self.assertTrue(all("2330" in a["message"] for a in alerts))
            self.assertTrue(all(a["chat_id"] == 42 for a in alerts))

            # second run: already-alerted levels must not repeat
            self.assertEqual(check_portfolio(store, state), [])
            alerted = PortfolioStore(os.path.join(temp_dir, "portfolio.json")).list()["2330"]["alerted"]
            for level in expected:
                self.assertTrue(alerted[level])

    def test_stop_loss_alert(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            bars = _bars()
            # crash the last bars below the platform stop
            for i in range(5):
                bars[-(i + 1)]["close"] = 90.0
                bars[-(i + 1)]["low"] = 89.0
                bars[-(i + 1)]["high"] = 91.0
            plan = compute_trade_plan(bars)
            self.assertIsNotNone(plan)
            state = FakeState({"2330": {"prices": bars}})

            alerts = check_portfolio(store, state)
            levels = {a["level"] for a in alerts}
            self.assertIn("stop_loss", levels)
            self.assertEqual(levels, _expected_levels(plan, bars[-1]["close"]))
            self.assertEqual(check_portfolio(store, state), [])

    def test_no_alert_when_plan_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            state = FakeState({"2330": {"prices": [{"date": "d", "close": 10, "volume": 1}] * 10}})
            self.assertEqual(check_portfolio(store, state), [])
            state_empty = FakeState({})
            self.assertEqual(check_portfolio(store, state_empty), [])
            self.assertFalse(any(store.list()["2330"]["alerted"].values()))


class FakeYahoo:
    def __init__(self, events):
        self.events = events

    def fetch_dividend_events(self, ticker):
        return self.events.get(ticker, [])


class DividendAlertTests(unittest.TestCase):
    def test_alert_once_within_window_and_dedupe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "portfolio.json")
            store = PortfolioStore(path)
            store.add("0050", 100.0, "2026-11-01", 42)
            ex_date = (date.today() + timedelta(days=3)).isoformat()
            yahoo = FakeYahoo({"0050": [{"date": ex_date, "amount": 1.5}]})

            alerts = check_dividend_alerts(store, yahoo, today=date.today())
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["level"], "dividend")
            self.assertIn(ex_date, alerts[0]["message"])
            self.assertIn("最後買進日", alerts[0]["message"])
            self.assertEqual(alerts[0]["chat_id"], 42)

            self.assertEqual(check_dividend_alerts(store, yahoo, today=date.today()), [])
            persisted = PortfolioStore(path).list()["0050"]["alerted"]
            self.assertTrue(persisted[f"dividend_{ex_date}"])

    def test_past_and_far_future_events_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PortfolioStore(os.path.join(temp_dir, "portfolio.json"))
            store.add("2330", 10.0, "2026-11-01", 1)
            yahoo = FakeYahoo({
                "2330": [
                    {"date": (date.today() - timedelta(days=10)).isoformat(), "amount": 7.0},
                    {"date": (date.today() + timedelta(days=30)).isoformat(), "amount": 7.0},
                ]
            })
            self.assertEqual(check_dividend_alerts(store, yahoo, today=date.today()), [])

    def test_last_buy_date_skips_weekend(self):
        from src.analysis.portfolio_monitor import _last_buy_date
        # ex-date Monday 2026-12-14 -> last buy day Friday 2026-12-11
        self.assertEqual(_last_buy_date(date(2026, 12, 14)), date(2026, 12, 11))


if __name__ == "__main__":
    unittest.main()
