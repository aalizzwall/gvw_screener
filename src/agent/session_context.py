import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger("gvw.agent.session")


class SessionContext:
    """
    Manages multi-turn conversation memory and context window compaction,
    inspired by Pi Agent's minimalist sliding context design.
    """

    def __init__(
        self,
        session_id: str,
        max_turns: int = 10,
        max_chars: int = 14000,
        max_observation_chars: int = 2500,
    ):
        self.session_id = session_id
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.max_observation_chars = max_observation_chars
        self.history: List[Dict[str, str]] = []

    def add_user_message(self, content: str):
        self.history.append({"role": "user", "content": content})
        self._compact_if_needed()

    def add_assistant_message(self, content: str):
        self.history.append({"role": "assistant", "content": content})
        self._compact_if_needed()

    def add_observation(self, action: str, tool_result: Any):
        tool_str = (
            json.dumps(tool_result, ensure_ascii=False)
            if isinstance(tool_result, (dict, list))
            else str(tool_result)
        )
        if len(tool_str) > self.max_observation_chars:
            tool_str = tool_str[: self.max_observation_chars] + "... (truncated)"

        content = (
            f"Observation from {action}:\n{tool_str}\n\n"
            "請根據以上真實觀測結果繼續分析。若資訊已足夠，請回傳 final_answer。"
        )
        self.history.append({"role": "user", "content": content})
        self._compact_if_needed()

    def get_messages(self, system_prompt: str) -> List[Dict[str, str]]:
        """Returns messages prepared for LLM API with system prompt prepended."""
        return [{"role": "system", "content": system_prompt}] + list(self.history)

    def _compact_if_needed(self):
        """
        Compacts dialogue history if it exceeds max turns or character limits.
        Retains the most recent conversational context while preserving continuity.
        """
        # Count user turns (excluding tool observations)
        user_turns = [
            idx
            for idx, msg in enumerate(self.history)
            if msg["role"] == "user" and not msg["content"].startswith("Observation from ")
        ]

        if len(user_turns) > self.max_turns:
            excess = len(user_turns) - self.max_turns
            cut_index = user_turns[excess]
            logger.info(
                f"Session [{self.session_id}] compacting: trimming {excess} earlier turns."
            )
            self.history = self.history[cut_index:]

        # Check total character length
        total_chars = sum(len(m.get("content", "")) for m in self.history)
        if total_chars > self.max_chars:
            logger.info(
                f"Session [{self.session_id}] context length ({total_chars} chars) exceeds {self.max_chars}, pruning oldest messages."
            )
            while total_chars > self.max_chars and len(self.history) > 2:
                # Remove oldest non-observation or observation pair
                removed = self.history.pop(0)
                total_chars -= len(removed.get("content", ""))

    def clear(self):
        """Clears all conversation memory for this session."""
        self.history.clear()

    @property
    def turn_count(self) -> int:
        return len([
            m for m in self.history
            if m["role"] == "user" and not m["content"].startswith("Observation from ")
        ])

