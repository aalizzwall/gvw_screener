import aiosqlite
import math
import logging
from typing import Any, Dict, List
from src.interfaces.backtest import BacktestEngine

logger = logging.getLogger(__name__)

class SQLiteBacktestEngine(BacktestEngine):
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def run_backtest(self, start_date: str, end_date: str) -> Dict[str, Any]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT score, timestamp FROM scoring_results WHERE timestamp BETWEEN ? AND ?",
                    (start_date, end_date)
                ) as cursor:
                    results = await cursor.fetchall()

            if not results:
                return self._empty_metrics()

            # results is a list of (score, timestamp)
            # We assume score represents a return or a performance metric that can be used for calculation
            # For a real backtest, we'd typically need price data to calculate returns.
            # Given the schema, we will treat 'score' as a proxy for performance metric for this implementation.
            # In a real scenario, we would join with price data.
            
            scores = [row[0] for row in results]
            
            win_rate = self._calculate_win_rate(scores)
            sharpe_ratio = self._calculate_sharpe_ratio(scores)
            max_drawdown = self._calculate_max_drawdown(scores)
            total_return = self._calculate_total_return(scores)

            return {
                "win_rate": win_rate,
                "sharpe_ratio": sharpe_ratio,
                "max_drawdown": max_drawdown,
                "total_return": total_return,
                "sample_size": len(scores)
            }

        except Exception as e:
            logger.error(f"Error during backtest execution: {e}")
            return self._empty_metrics()

    def _calculate_win_rate(self, scores: List[float]) -> float:
        if not scores:
            return 0.0
        wins = sum(1 for s in scores if s > 0)
        return wins / len(scores)

    def _calculate_sharpe_ratio(self, scores: List[float]) -> float:
        if len(scores) < 2:
            return 0.0
        
        mean_return = sum(scores) / len(scores)
        variance = sum((x - mean_return) ** 2 for x in scores) / (len(scores) - 1)
        
        if variance == 0:
            return 0.0
            
        return mean_return / math.sqrt(variance)

    def _calculate_max_drawdown(self, scores: List[float]) -> float:
        if not scores:
            return 0.0
        
        # Assuming scores are cumulative or we treat them as performance increments.
        # For drawdown, we need a cumulative equity curve.
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        
        for s in scores:
            equity *= (1 + s)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
                
        return max_dd

    def _calculate_total_return(self, scores: List[float]) -> float:
        equity = 1.0
        for s in scores:
            equity *= (1 + s)
        return equity - 1.0

    def _empty_metrics(self) -> Dict[str, Any]:
        return {
            "win_rate": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "total_return": 0.0,
            "sample_size": 0
        }
