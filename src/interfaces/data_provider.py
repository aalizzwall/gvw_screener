from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime

class DataProvider(ABC):
    """
    Interface for data acquisition from various external sources.
    """

    @abstractmethod
    def fetch_stock_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch comprehensive data for a given ticker.
        """
        pass

    @abstractmethod
    def fetch_market_index(self, index_ticker: str) -> Dict[str, Any]:
        """
        Fetch market index data.
        """
        pass

    @abstractmethod
    def check_availability(self) -> bool:
        """
        Check if the data provider is accessible.
        """
        pass
