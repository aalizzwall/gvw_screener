import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.analysis.ranker import DEFAULT_SCORE_WEIGHTS, load_score_weights
from src.autopilot.ledger import RecommendationLedger
from src.autopilot.pilot import AutoPilot
from src.state.state_manager import StateManager


def _days(n, start="2026-10-01"):
    d = datetime.strptime(start, "%Y-%m-%d")
    return [(d + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = StateManager(data_dir=os.path.join(self.tmp.name, "data"))
        dates = _days(30)
        # 2330 rises 1%/day, 1101 falls 1%/day
        self.state.save_stock_data("2330", {"ticker": "2330", "prices": [
            {"date": d, "close": 100 * (1.01 ** i)} for i, d in enumerate(dates)]})
        self.state.save_stock_data("1101", {"ticker": "1101", "prices": [
            {"date": d, "close": 100 * (0.99 ** i)} for i, d in enumerate(dates)]})
        self.ledger = RecommendationLedger(self.state, path=os.path.join(self.tmp.name, "rec.json"))
        self.ranks = {"data": [
            {"ticker": "2330", "name": "TSMC", "final_score": 0.9, "entry_signal": "A", "m_rank": 0.9, "t_score": 0.8, "q_rank": 0.7},
            {"ticker": "1101", "name": "CCC", "final_score": 0.5, "entry_signal": "B", "m_rank": 0.3, "t_score": 0.2, "q_rank": 0.4},
        ]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_is_idempotent_and_evaluate_fills_outcomes(self):
        self.assertEqual(self.ledger.record(self.ranks, as_of="2026-10-05"), 2)
        self.assertEqual(self.ledger.record(self.ranks, as_of="2026-10-05"), 2)
        with open(self.ledger.path) as f:
            self.assertEqual(len(json.load(f)), 1)
        self.assertEqual(self.ledger.evaluate(), 6)  # 2 picks x 3 horizons
        s = self.ledger.summary()
        self.assertEqual(s["horizons"][5]["n"], 2)
        self.assertEqual(s["horizons"][5]["win_rate"], 0.5)
        self.assertEqual(s["by_signal"]["A"]["win_rate"], 1.0)

    def test_unmatured_horizons_stay_pending(self):
        self.ledger.record(self.ranks, as_of="2026-10-30")
        self.assertEqual(self.ledger.evaluate(), 0)

    def test_excess_return_vs_benchmark_and_report_section(self):
        from src.autopilot.benchmark import BenchmarkSeries
        from src.autopilot.ledger import format_performance_section

        bench = BenchmarkSeries(path=os.path.join(self.tmp.name, "bench.json"))
        for i, d in enumerate(_days(30)):
            bench.closes[d] = 1000 * (1.002 ** i)  # market +0.2%/day
        self.ledger.record(self.ranks, as_of="2026-10-05")
        self.ledger.evaluate(bench)
        s = self.ledger.summary()["horizons"][1]
        self.assertEqual(s["n_excess"], 2)
        self.assertAlmostEqual(s["avg_excess"], -0.002, places=4)  # avg raw 0% minus market +0.2%
        self.assertEqual(s["beat_rate"], 0.5)
        text = format_performance_section(self.ledger.summary())
        self.assertIn("相對大盤", text)
        self.assertEqual(format_performance_section({"horizons": {1: {"n": 0}}}), "")

    def test_benchmark_ingests_twse_snapshot_and_computes_return(self):
        from src.autopilot.benchmark import BenchmarkSeries

        b = BenchmarkSeries(path=os.path.join(self.tmp.name, "b.json"))
        snap = {"stats": [{"日期": "1151022", "指數": "發行量加權股價指數", "收盤指數": "46,041.43"}]}
        self.assertTrue(b.ingest_market_index(snap))
        self.assertEqual(b.closes["2026-10-22"], 46041.43)
        b.closes["2026-10-23"] = 46041.43 * 1.01
        self.assertAlmostEqual(b.ret("2026-10-22", "2026-10-23"), 0.01)
        self.assertIsNone(b.ret("2026-10-22", "2026-12-01"))
        self.assertFalse(b.ingest_market_index({}))

    def test_propose_weights_needs_samples(self):
        self.ledger.record(self.ranks, as_of="2026-10-05")
        self.ledger.evaluate()
        self.assertIsNone(self.ledger.propose_weights(min_samples=150))

    def test_propose_weights_shifts_toward_predictive_factor(self):
        entries = []
        for i in range(60):
            picks = []
            for j in range(5):
                m = j / 5
                picks.append({"ticker": f"{j}", "m_rank": m, "t_score": ((j * 3) % 5) / 5, "q_rank": 1 - m,
                              "outcomes": {"5": m * 0.1 - 0.02 + i * 1e-4}})
            entries.append({"date": f"d{i}", "picks": picks})
        with open(self.ledger.path, "w") as f:
            json.dump(entries, f)
        with patch("src.autopilot.ledger.load_score_weights", return_value=dict(DEFAULT_SCORE_WEIGHTS)):
            p = self.ledger.propose_weights(min_samples=100)
        self.assertIsNotNone(p)
        self.assertGreater(p["proposed"]["momentum"], p["current"]["momentum"])
        self.assertLess(p["proposed"]["quality"], p["current"]["quality"])
        self.assertAlmostEqual(sum(p["proposed"].values()), 1.0, places=2)


class TestAutoPilot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = StateManager(data_dir=os.path.join(self.tmp.name, "data"))
        self.doctor = MagicMock()
        self.orch = MagicMock()
        self.rank = MagicMock(return_value={"data": []})
        self.notify = MagicMock(return_value=True)
        self.pilot = AutoPilot(self.state, self.doctor, self.orch, self.rank,
                               notify=self.notify, data_dir=self.tmp.name)
        patcher = patch("src.autopilot.pilot.TELEGRAM_OWNER_USER_ID", "42")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.healthy = {"memory": {"percent": 40}, "disk": {"percent": 40}, "cpu_temp": 50,
                        "database": {"all_ok": True}, "heartbeat": {"exists": True, "fresh": True},
                        "connectivity": {"telegram": {"ok": True}, "twse": {"ok": True},
                                         "ai": {"primary": "g", "primary_ok": True}}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_issues(self):
        bad = json.loads(json.dumps(self.healthy))
        bad["memory"]["percent"] = 95
        bad["cpu_temp"] = 80
        bad["connectivity"]["twse"]["ok"] = False
        issues = self.pilot.find_issues(bad)
        self.assertEqual(len(issues), 3)
        self.assertEqual(self.pilot.find_issues(self.healthy), [])

    def test_guard_silent_when_healthy(self):
        self.doctor.run_diagnostics.return_value = self.healthy
        with patch.object(self.pilot, "os_health", return_value=[]):
            res = self.pilot.intraday_guard()
        self.assertEqual(res["issues"], [])
        self.notify.assert_not_called()
        self.doctor.run_remediation.assert_not_called()

    def test_guard_heals_light_alerts_once_and_escalates(self):
        bad = json.loads(json.dumps(self.healthy))
        bad["connectivity"]["twse"]["ok"] = False
        self.doctor.run_diagnostics.return_value = bad
        self.doctor.run_remediation.return_value = ["已清理 1 個殘留鎖檔 (*.lock)"]
        self.doctor.report_unresolved_issue.return_value = (True, "http://issue/1")
        with patch.object(self.pilot, "os_health", return_value=[]):
            for _ in range(3):
                self.pilot.intraday_guard()
        self.doctor.run_remediation.assert_called_with(light=True)
        # alert cooldown: first patrol alerts, 3rd patrol sends the escalation notice
        texts = [c.args[0] for c in self.notify.call_args_list]
        self.assertEqual(sum("偵測到異常" in t for t in texts), 1)
        self.assertEqual(sum("已建立 Issue" in t for t in texts), 1)
        self.doctor.report_unresolved_issue.assert_called_once()

    def test_premarket_refreshes_stale_data(self):
        self.state.save_stock_data("A", {"prices": [{"date": "2026-10-26", "close": 1}]})
        self.state.save_stock_data("B", {"prices": [{"date": "2026-10-20", "close": 1}]})
        stale_then_fresh = [
            {"total": 2, "stale": 1, "ratio": 0.5, "reference": "2026-10-26"},
            {"total": 2, "stale": 0, "ratio": 0.0, "reference": "2026-10-26"},
        ]
        with patch.object(self.pilot, "data_staleness", side_effect=stale_then_fresh):
            res = self.pilot.premarket_check()
        self.assertTrue(res["refreshed"])
        self.orch.run_collection_cycle.assert_called_once()
        self.rank.assert_called_once()
        self.notify.assert_not_called()

    def test_data_staleness_ignores_dead_tickers(self):
        self.state.save_stock_data("A", {"prices": [{"date": "2026-10-26", "close": 1}]})
        self.state.save_stock_data("B", {"prices": [{"date": "2026-10-20", "close": 1}]})
        self.state.update_health_status({"B": {"status": "dead"}})
        res = self.pilot.data_staleness()
        self.assertEqual((res["total"], res["stale"]), (1, 0))

    def test_apply_and_dismiss_proposal(self):
        proposal = {"proposed": {"momentum": 0.5, "trend": 0.2, "quality": 0.3}}
        self.pilot._save_proposal(proposal)
        self.assertEqual(self.pilot.apply_proposal(), proposal["proposed"])
        self.assertEqual(load_score_weights(os.path.join(self.tmp.name, "tuning_weights.json")),
                         proposal["proposed"])
        self.assertIsNone(self.pilot.apply_proposal())
        self.pilot._save_proposal(proposal)
        self.assertTrue(self.pilot.dismiss_proposal())
        self.assertFalse(self.pilot.dismiss_proposal())

    def test_digest_is_single_message_and_clears_events(self):
        self.pilot._record("測試事件")
        self.pilot.send_digest({"horizons": {1: {"n": 0}, 5: {"n": 0}, 20: {"n": 0}}, "by_signal": {}})
        self.notify.assert_called_once()
        self.assertIn("測試事件", self.notify.call_args.args[0])
        self.assertEqual(self.pilot.events, [])


class TestRankerWeights(unittest.TestCase):
    def test_defaults_and_invalid_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.json")
            self.assertEqual(load_score_weights(p), DEFAULT_SCORE_WEIGHTS)
            with open(p, "w") as f:
                f.write("not json")
            self.assertEqual(load_score_weights(p), DEFAULT_SCORE_WEIGHTS)


if __name__ == "__main__":
    unittest.main()
