from typing import Dict, Any, List
from src.state.interfaces import IStateRepository

class InMemoryStateRepository(IStateRepository):
    """
    In-memory implementation of IStateRepository for testing purposes.
    """
    def __init__(self):
        self._stock_data: Dict[str, Dict[str, Any]] = {}
        self._health_status: Dict[str, Any] = {}
        self._universe: Dict[str, str] = {}
        self._ranks: Dict[str, Any] = {"data": []}
        self._market_index: Dict[str, Any] = {}

    def get_stock_data(self, ticker: str) -> Dict[str, Any]:
        return self._stock_data.get(ticker, {}).copy()

    def save_stock_data(self, ticker: str, data: Dict[str, Any]):
        self._stock_data[ticker] = data.copy()

    def list_stock_tickers(self) -> List[str]:
        return list(self._stock_data.keys())

    def get_universe(self) -> Dict[str, str]:
        return self._universe.copy()

    def save_universe(self, universe_map: Dict[str, str]):
        self._universe = universe_map.copy()

    def get_health_status(self) -> Dict[str, Any]:
        return self._health_status.copy()

    def update_health_status(self, health_status: Dict[str, Any]):
        self._health_status = health_status.copy()

    def update_stock_health(self, ticker: str, status_update: Dict[str, Any]):
        if ticker not in self._health_status:
            self._health_status[ticker] = {}
        self._health_status[ticker].update(status_update)

    def get_ranks(self) -> Dict[str, Any]:
        return self._ranks.copy()

    def save_ranks(self, ranks_data: Dict[str, Any]):
        self._ranks = ranks_data.copy()

    def get_market_index(self) -> Dict[str, Any]:
        return self._market_index.copy()

    def save_market_index(self, index_data: Dict[str, Any]):
        self._market_index = index_data.copy()
