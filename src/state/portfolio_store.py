import json
import logging
import os
from typing import Any, Dict, Optional

from src.config import DATA_DIR

logger = logging.getLogger(__name__)


class PortfolioStore:
    """User watchlist: ticker -> {entry_price, entry_date, chat_id, alerted{...}}."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(DATA_DIR, "user_portfolio.json")
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._entries = {
                        str(k).strip(): v
                        for k, v in data.items()
                        if isinstance(v, dict) and isinstance(v.get("entry_price"), (int, float))
                    }
            except Exception as e:
                logger.warning(f"[PortfolioStore] Failed to load {self.path}: {e}")

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[PortfolioStore] Failed to save {self.path}: {e}")

    def add(self, ticker: str, entry_price: float, entry_date: str, chat_id: Any) -> bool:
        self._load()
        t = str(ticker).strip()
        if not t:
            return False
        self._entries[t] = {
            "entry_price": float(entry_price),
            "entry_date": entry_date,
            "chat_id": chat_id,
            "alerted": {"target_1": False, "target_2": False, "stop_loss": False},
        }
        self._save()
        return True

    def remove(self, ticker: str) -> bool:
        self._load()
        t = str(ticker).strip()
        if t in self._entries:
            del self._entries[t]
            self._save()
            return True
        return False

    def list(self) -> Dict[str, Dict[str, Any]]:
        self._load()
        return dict(self._entries)

    def save(self) -> None:
        self._save()
