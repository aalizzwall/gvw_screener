import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from src.config import (
    DATA_DIR,
    HEALTH_STATUS_FILE,
    UNIVERSE_FILE,
    RESULT_FILE,
    MARKET_INDEX_FILE,
    POINTER_FILE
)

logger = logging.getLogger(__name__)

class StateManager:
    """
    Manages application state, including stock data persistence,
    universe mapping, health tracking, and rankings.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        health_file: Optional[str] = None,
        universe_file: Optional[str] = None,
        result_file: Optional[str] = None,
        market_index_file: Optional[str] = None
    ):
        self.data_dir = data_dir or DATA_DIR
        self.health_file = health_file or HEALTH_STATUS_FILE
        self.universe_file = universe_file or UNIVERSE_FILE
        self.result_file = result_file or RESULT_FILE
        self.market_index_file = market_index_file or MARKET_INDEX_FILE
        self._ensure_dir()

    def _ensure_dir(self):
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)

    def _load_json_safe(self, path: str, default: Any) -> Any:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading {path}: {e}")
        return default

    def _save_json(self, path: str, data: Any):
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving {path}: {e}")

    def get_stock_data(self, ticker: str) -> Dict[str, Any]:
        path = os.path.join(self.data_dir, f"{ticker}.json")
        return self._load_json_safe(path, {})

    def save_stock_data(self, ticker: str, data: Dict[str, Any]):
        path = os.path.join(self.data_dir, f"{ticker}.json")
        self._save_json(path, data)

    def list_stock_files(self) -> List[str]:
        if not os.path.exists(self.data_dir):
            return []
        return [f[:-5] for f in os.listdir(self.data_dir) if f.endswith('.json')]

    def get_universe(self) -> Dict[str, str]:
        data = self._load_json_safe(self.universe_file, {})
        if isinstance(data, list):
            # Convert list of tickers or dicts to map
            res = {}
            for item in data:
                if isinstance(item, dict):
                    res[str(item.get('ticker', item.get('code', '')))] = str(item.get('name', ''))
                else:
                    res[str(item)] = ""
            return res
        return data

    def save_universe(self, universe_map: Dict[str, str]):
        self._save_json(self.universe_file, universe_map)

    def get_health_status(self) -> Dict[str, Any]:
        return self._load_json_safe(self.health_file, {})

    def update_health_status(self, health_status: Dict[str, Any]):
        self._save_json(self.health_file, health_status)

    def update_stock_health(self, ticker: str, status_update: Dict[str, Any]):
        health = self.get_health_status()
        if ticker not in health:
            health[ticker] = {}
        health[ticker].update(status_update)
        self.update_health_status(health)

    def get_ranks(self) -> Dict[str, Any]:
        return self._load_json_safe(self.result_file, {"data": []})

    def save_ranks(self, ranks_data: Dict[str, Any]):
        self._save_json(self.result_file, ranks_data)

    def get_market_index(self) -> Dict[str, Any]:
        return self._load_json_safe(self.market_index_file, {})

    def save_market_index(self, index_data: Dict[str, Any]):
        self._save_json(self.market_index_file, index_data)
