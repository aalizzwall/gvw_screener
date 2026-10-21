import os
import json
import logging
from typing import Set, List, Optional, Union
from src.config import DATA_DIR

logger = logging.getLogger(__name__)


class AlertSubscriptionStore:
    """
    Manages subscriber chat IDs for intraday monitoring alerts.
    Persisted to JSON in DATA_DIR.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(DATA_DIR, "alert_subscribers.json")
        self._subscribers: Set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._subscribers = {str(item).strip() for item in data if item}
            except Exception as e:
                logger.warning(f"Failed to load alert subscribers: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(sorted(list(self._subscribers)), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save alert subscribers: {e}")

    def is_subscribed(self, chat_id: Union[int, str]) -> bool:
        self._load()
        return str(chat_id).strip() in self._subscribers

    def subscribe(self, chat_id: Union[int, str]) -> bool:
        self._load()
        cid = str(chat_id).strip()
        if cid not in self._subscribers:
            self._subscribers.add(cid)
            self._save()
            return True
        return False

    def unsubscribe(self, chat_id: Union[int, str]) -> bool:
        self._load()
        cid = str(chat_id).strip()
        if cid in self._subscribers:
            self._subscribers.remove(cid)
            self._save()
            return True
        return False

    def get_subscribers(self) -> List[str]:
        self._load()
        return sorted(list(self._subscribers))
