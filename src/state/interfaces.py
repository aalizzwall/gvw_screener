from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class IStateRepository(ABC):
    """
    Abstract Interface for State Management.
    Decouples the Orchestrator from the filesystem.
    """
    @abstractmethod
    def get_stock_data(self, ticker: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def save_stock_data(self, ticker: str, data: Dict[str, Any]):
        pass

    @abstractmethod
    def list_stock_tickers(self) -> List[str]:
        pass

    @abstractmethod
    def get_universe(self) -> Dict[str, str]:
        pass

    @abstractmethod
    def save_universe(self, universe_map: Dict[str, str]):
        pass

    @abstractmethod
    def get_health_status(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def update_health_status(self, health_status: Dict[str, Any]):
        pass

    @abstractmethod
    def update_stock_health(self, ticker: str, status_update: Dict[str, Any]):
        pass

    @abstractmethod
    def get_ranks(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def save_ranks(self, ranks_data: Dict[str, Any]):
        pass

    @abstractmethod
    def get_market_index(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def save_market_index(self, index_data: Dict[str, Any]):
        pass
