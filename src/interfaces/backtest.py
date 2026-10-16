from abc import ABC, abstractmethod
from typing import Any, Dict

class BacktestEngine(ABC):
    @abstractmethod
    async def run_backtest(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Execute a backtest for the given date range.
        
        Args:
            start_date: Start date in 'YYYY-MM-DD' format.
            end_date: End date in 'YYYY-MM-DD' format.
            
        Returns:
            A dictionary containing performance metrics.
        """
        pass
