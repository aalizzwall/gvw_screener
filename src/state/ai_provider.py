import json
import os
from typing import List

from src.config import AI_PROVIDER_FILE

PROVIDER_NAMES = ("openai", "gemma")
DEFAULT_PRIMARY = "openai"


class AIProviderPreferenceStore:
    """Persist which AI provider the owner selected as primary; the rest are fallbacks."""

    def __init__(self, path: str = AI_PROVIDER_FILE):
        self.path = path

    def get_primary(self) -> str:
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                primary = json.load(state_file).get("primary")
        except (FileNotFoundError, ValueError, AttributeError):
            return DEFAULT_PRIMARY
        return primary if primary in PROVIDER_NAMES else DEFAULT_PRIMARY

    def set_primary(self, provider: str) -> None:
        if provider not in PROVIDER_NAMES:
            raise ValueError(f"Unknown AI provider: {provider}")
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp_path = f"{self.path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as state_file:
            json.dump({"primary": provider}, state_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)

    def get_order(self) -> List[str]:
        primary = self.get_primary()
        return [primary] + [name for name in PROVIDER_NAMES if name != primary]
