from abc import ABC, abstractmethod
from typing import Any, Dict, List

class Optimizer(ABC):
    @abstractmethod
    async def optimize(self, historical_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Find the optimal parameters based on historical data.

        Args:
            historical_data: A list of historical data points used for optimization.

        Returns:
            A dictionary containing the optimal parameters and performance metrics.
        """
        pass
