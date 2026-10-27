import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.config import FEEDBACK_QUEUE_FILE


class FeedbackQueueStore:
    """
    Persists user-submitted error reports (capturing the last 3 bot replies and user context)
    and tracks their triage status for self-patrol (AutoPilot) evaluation.
    """

    def __init__(self, path: str = FEEDBACK_QUEUE_FILE):
        self.path = path

    def add_report(
        self,
        chat_id: Any,
        user_id: Any,
        bot_reply: str = "",
        user_input: str = "",
        note: str = "",
        bot_replies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        data = self._load()
        reports = data.get("reports", [])
        report_id = len(reports) + 1

        replies_list = [
            str(r).strip()[:2000]
            for r in (bot_replies if bot_replies is not None else ([bot_reply] if bot_reply else []))
            if str(r).strip()
        ][-3:]
        latest_reply = replies_list[-1] if replies_list else str(bot_reply or "").strip()[:2000]
        combined_text = f"{user_input} " + " ".join(replies_list)
        ticker_match = re.search(r"\b(\d{4,6})\b", combined_text)
        detected_ticker = ticker_match.group(1) if ticker_match else ""

        entry = {
            "id": report_id,
            "created_at": datetime.now().isoformat(),
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "user_input": str(user_input or "").strip()[:500],
            "bot_reply": latest_reply,
            "bot_replies": replies_list,
            "note": str(note or "").strip()[:1000],
            "ticker": detected_ticker,
            "status": "pending",
            "resolution": "",
            "resolved_at": None,
        }
        reports.append(entry)
        # Keep most recent 200 reports
        data["reports"] = reports[-200:]
        self._save(data)
        return entry

    def append_note_to_latest(self, chat_id: Any, note: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        reports = data.get("reports", [])
        target_chat = str(chat_id)
        for entry in reversed(reports):
            if entry.get("chat_id") == target_chat and entry.get("status") == "pending":
                existing = entry.get("note", "")
                entry["note"] = (f"{existing}\n{note}".strip() if existing else note.strip())[:1000]
                self._save(data)
                return entry
        return None

    def pending_reports(self) -> List[Dict[str, Any]]:
        return [
            r for r in self._load().get("reports", [])
            if isinstance(r, dict) and r.get("status") == "pending"
        ]

    def mark_resolved(self, report_id: int, status: str, resolution: str) -> bool:
        data = self._load()
        updated = False
        for entry in data.get("reports", []):
            if entry.get("id") == report_id:
                entry["status"] = status
                entry["resolution"] = resolution
                entry["resolved_at"] = datetime.now().isoformat()
                updated = True
                break
        if updated:
            self._save(data)
        return updated

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("reports"), list):
                    return data
        except (FileNotFoundError, ValueError):
            # Return empty structure when queue file is missing or malformed
            pass
        return {"reports": []}

    def _save(self, data: Dict[str, Any]) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp_path = f"{self.path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)
