import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class StateManager:
    """
    Manages application state, including stock data persistence and health tracking.
    """

    def __init__(self, data_dir: str, health_file: str):
        self.data_dir = data_dir
        self.health_file = health_file
        self._ensure_dir()

    def _ensure_dir(self):
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

    def get_stock_data(self, ticker: str) -> Dict[str, Any]:
        path = os.path.join(self.data_dir, f"{ticker}.json")
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading stock data for {ticker}: {e}")
        return {}

    def save_stock_data(self, ticker: str, data: Dict[str, Any]):
        path = os.path.join(self.data_dir, f"{ticker}.json")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving stock data for {ticker}: {e}")

    def get_health_status(self) -> Dict[str, Any]:
        if os.path.exists(self.health_file):
            try:
                with open(self.health_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading health status: {e}")
        return {}

    def update_health_status(self, health_status: Dict[str, Any]):
        try:
            with open(self.health_file, 'w', encoding='utf-8') as f:
                json.dump(health_status, f, indent=4)
        except Exception as e:
            logger.error(f"Error updating health status: {e}")

    def update_stock_health(self, ticker: str, status_update: Dict[str, Any]):
        health = self.get_health_status()
        if ticker not in health:
            health[ticker] = {}
        health[ticker].update(status_update)
        self.update_health_status(health)
