import unittest
import pandas as pd
import numpy as np
from src.core.orchestrator import Orchestrator
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

if __name__ == "__main__":
    unittest.main()

