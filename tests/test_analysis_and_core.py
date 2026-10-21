import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import pandas as pd
import numpy as np
from src.core.orchestrator import Orchestrator
from src.analysis.intraday import run_intraday_monitoring
from src.analysis.ranker import (
    calculate_momentum_score,
    calculate_trend_score,
    calculate_entry_signal,
    calculate_quality_score
)
from src.analysis.signals import check_kline_three_steps
from src.reporting.report_engine import get_suggested_price
from src.state.state_manager import StateManager
from src.providers.factory import DataProviderFactory
from src.providers.yahoo_adapter import TickerNotFoundError

class TestAnalysisAndCore(unittest.TestCase):

    def test_calculate_quality_score(self):
        orchestrator = Orchestrator()
        pl = {'revenue': 1000000, 'net_income': 200000}
        bs = {'total_assets': 2000000, 'total_liabilities': 500000}
        score = orchestrator.calculate_quality_score(pl, bs)
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_momentum_and_trend(self):
        # 300 days of price data
        dates = pd.date_range(end='2026-10-14', periods=300)
        prices = np.linspace(100, 200, 300)
        df_prices = pd.DataFrame({'date': dates, 'close': prices})

        m_score = calculate_momentum_score(200.0, prices)
        self.assertGreater(m_score, 0.0)

        t_score, is_strict = calculate_trend_score(df_prices)
        self.assertGreater(t_score, 0.0)

        signal = calculate_entry_signal(df_prices)
        self.assertIn(signal, ["🟢 回調買入區", "🟡 過熱-待回調", "🔴 趨勢偏弱", "⚪ 中立/觀察", "數據不足"])

    def test_factory_initialization(self):
        factory = DataProviderFactory()
        self.assertIsNotNone(factory.twse)
        self.assertIsNotNone(factory.finmind)
        self.assertIsNotNone(factory.yahoo)

    def test_intraday_uses_local_prices_when_ticker_is_missing(self):
        state = Mock()
        state.get_ranks.return_value = {
            'data': [{'ticker': '2330', 'name': 'TSMC', 'final_score': 0.9, 't_score': 1.0}]
        }
        state.get_stock_data.return_value = {
            'prices': [
                {'date': str(day), 'close': 100, 'volume': 100 if day < 16 else 50}
                for day in range(21)
            ]
        }
        factory = Mock()
        factory.fetch_price_history.side_effect = TickerNotFoundError('2330')

        with TemporaryDirectory() as temp_dir:
            with patch('src.analysis.intraday.SIGNAL_CACHE_FILE', f'{temp_dir}/signals.json'):
                alerts = run_intraday_monitoring(state, factory)

        saved_report = state.save_ranks.call_args.args[0]
        self.assertEqual(saved_report['data'][0]['kline_status'], 'ACCUMULATING')
        self.assertEqual(len(alerts), 1)

    def test_intraday_scans_top_30_candidates(self):
        state = Mock()
        stocks = [
            {'ticker': str(1000 + i), 'name': f'Stock{i}', 'final_score': float(i)}
            for i in range(40)
        ]
        state.get_ranks.return_value = {'data': stocks}
        state.get_stock_data.return_value = {'prices': []}
        factory = Mock()
        factory.fetch_price_history.return_value = []

        with TemporaryDirectory() as temp_dir:
            with patch('src.analysis.intraday.SIGNAL_CACHE_FILE', f'{temp_dir}/signals.json'):
                run_intraday_monitoring(state, factory)

        self.assertEqual(factory.fetch_price_history.call_count, 30)

if __name__ == "__main__":
    unittest.main()
