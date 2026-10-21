import json
import os
from typing import Any, Dict, List, Optional

from src.config import AI_ACCESS_FILE


class AIAnalysisAccessStore:
    """Persist AI-analysis approvals and pending user requests."""

    def __init__(self, path: str = AI_ACCESS_FILE):
        self.path = path

    def is_approved(self, user_id: Any) -> bool:
        return str(user_id) in self._load()["approved"]

    def get_request(self, user_id: Any) -> Optional[Dict[str, Any]]:
        return self._load()["requests"].get(str(user_id))

    def pending_requests(self) -> List[Dict[str, Any]]:
        return [
            {"user_id": user_id, **request}
            for user_id, request in self._load()["requests"].items()
            if request.get("status") == "pending"
        ]

    def request(
        self,
        user_id: Any,
        chat_id: Any,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
    ) -> str:
        user_id = str(user_id)
        data = self._load()
        existing = data["requests"].get(user_id, {})
        if user_id in data["approved"]:
            return "approved"
        if existing.get("status") == "pending":
            return "pending"

        data["requests"][user_id] = {
            "status": "pending",
            "chat_id": str(chat_id),
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
        }
        self._save(data)
        return "created"

    def decide(self, user_id: Any, approved: bool) -> bool:
        user_id = str(user_id)
        data = self._load()
        request = data["requests"].get(user_id)
        if not request or request.get("status") != "pending":
            return False

        request["status"] = "approved" if approved else "rejected"
        if approved:
            data["approved"] = sorted(set(data["approved"]) | {user_id})
        else:
            data["approved"] = [item for item in data["approved"] if item != user_id]
        self._save(data)
        return True

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
        except FileNotFoundError:
            return {"approved": [], "requests": {}}
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("approved"), list)
            or not isinstance(data.get("requests"), dict)
        ):
            raise ValueError(f"Invalid AI access state file: {self.path}")
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp_path = f"{self.path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as state_file:
            json.dump(data, state_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)
