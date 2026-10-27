import json
import os
from typing import Any, Dict, List, Optional, Tuple

from src.config import (
    AI_PROVIDER_FILE,
    GEMMA_MODEL,
    LOCAL_AI_BASE_URL,
    LOCAL_AI_MODEL,
    OPENAI_MODEL,
)

PROVIDER_NAMES = ("openai", "gemma", "local")
PROVIDER_ALIASES = {"lmstudio": "local", "lm-studio": "local"}

# Models the owner may select, per provider. Strongest first. The models configured
# through OPENAI_MODEL / GEMMA_MODEL / LOCAL_AI_MODEL are always allowed as well.
_BUILTIN_MODELS: Dict[str, List[str]] = {
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ],
    "gemma": [
        "gemini-1.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-exp",
        "gemini-1.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemma-2-27b-it",
        "gemma-2-9b-it",
        "gemma-2-2b-it",
        "gemma-3-27b-it",
        "gemma-3-12b-it",
        "gemma-3-4b-it",
    ],
    "local": ["active", "qwen-3.6-9b", "qwen2.5-7b", "llama-3.2-3b"],
}


def model_catalog() -> Dict[str, List[str]]:
    catalog = {name: list(models) for name, models in _BUILTIN_MODELS.items()}
    for name, configured in (
        ("openai", OPENAI_MODEL),
        ("gemma", GEMMA_MODEL),
        ("local", LOCAL_AI_MODEL),
    ):
        if configured and configured not in catalog[name]:
            catalog[name].append(configured)
    try:
        from src.analysis.model_prober import _PROBE_CACHE
        for key, (_, ok, models, _) in _PROBE_CACHE.items():
            prov = key.split(":", 1)[0]
            if ok and prov in catalog:
                for m in models:
                    if m not in catalog[prov]:
                        catalog[prov].append(m)
    except Exception:
        # Ignore cache probe failure and return base catalog
        pass
    return catalog


AI_POLICIES: Dict[str, Dict[str, Any]] = {
    "auto": {
        "title": "⚡ 自動強度階梯",
        "description": "跨雲綜合強度階梯優先，地端本機保底（推薦高可用/全自動）",
    },
    "gemini": {
        "title": "🌐 Gemini 優先階梯",
        "description": "Google Gemini 輕量至旗艦優先，OpenAI 備援，地端本機保底",
    },
    "openai": {
        "title": "⚡ OpenAI 優先階梯",
        "description": "OpenAI 輕量至旗艦優先，Gemini 備援，地端本機保底",
    },
    "local": {
        "title": "🏠 地端 Active 優先",
        "description": "本機 LM-Studio 優先，雲端 Flash 速援，地端本機保底",
    },
}


def build_strength_fallback_chain(policy: str = "auto") -> List[str]:
    """
    Build an ordered fallback chain based on model strength and latency tiers across providers.
    Ensures local/active is ALWAYS guaranteed at the tail end as the ultimate safety net.
    """
    p = str(policy or "auto").strip().lower()
    chain: List[str] = []

    if p in ("gemini", "gemini_first"):
        chain.extend([
            "gemma/gemini-3.5-flash",
            "gemma/gemini-3.5-flash-lite",
            "gemma/gemini-3.1-flash-lite",
            "openai/gpt-4o-mini",
            "local/active",
        ])
    elif p in ("openai", "openai_first"):
        chain.extend([
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "gemma/gemini-3.5-flash",
            "gemma/gemini-3.5-flash-lite",
            "local/active",
        ])
    elif p in ("local", "local_first"):
        chain.extend([
            "local/active",
            "gemma/gemini-3.5-flash",
            "openai/gpt-4o-mini",
            "local/active",
        ])
    else:
        # Default "auto": Gemini 3.5 Flash (strong) -> 3.5 Flash Lite -> 3.1 Flash Lite -> OpenAI -> Local
        chain.extend([
            "gemma/gemini-3.5-flash",
            "gemma/gemini-3.5-flash-lite",
            "gemma/gemini-3.1-flash-lite",
            "openai/gpt-4o-mini",
            "local/active",
        ])

    # De-duplicate while preserving order
    deduped: List[str] = []
    for entry in chain:
        if entry not in deduped:
            deduped.append(entry)

    # Guarantee local/active is ALWAYS at the tail end (地端保底)
    if "local/active" not in deduped:
        deduped.append("local/active")
    elif deduped[-1] != "local/active" and p not in ("local", "local_first"):
        deduped.remove("local/active")
        deduped.append("local/active")

    return deduped


AI_CHAIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "lite_ladder": {
        "title": "⚡ Gemini Lite 梯隊備援鏈",
        "description": "Flash-Lite 多階梯速援 + 地端保底（推薦高可用/低延遲）",
        "chain": [
            "gemma/gemini-3.5-flash-lite",
            "gemma/gemini-3.1-flash-lite",
            "gemma/gemini-2.5-flash-lite",
            "gemma/gemini-3.5-flash",
            "local/active",
        ],
    },
    "standard": {
        "title": "🧠 Gemini Standard 均衡鏈",
        "description": "高精度 Flash 主力 + Lite 備援 + 地端保底",
        "chain": [
            "gemma/gemini-3.5-flash",
            "gemma/gemini-3.5-flash-lite",
            "local/active",
        ],
    },
    "local_first": {
        "title": "🏠 地端 Active 優先鏈",
        "description": "本機 LM-Studio 優先 + 雲端 Flash 備援",
        "chain": [
            "local/active",
            "gemma/gemini-3.5-flash-lite",
        ],
    },
}


def default_chain() -> List[str]:
    return [
        f"openai/{OPENAI_MODEL}",
        f"gemma/{GEMMA_MODEL}",
        f"local/{LOCAL_AI_MODEL}",
    ]


def parse_entry(entry: str) -> Tuple[str, str]:
    """Validate a ``provider/model`` entry against the whitelist and return its parts."""
    provider, sep, model = str(entry).strip().partition("/")
    provider = provider.lower()
    provider = PROVIDER_ALIASES.get(provider, provider)
    if not sep or provider not in PROVIDER_NAMES:
        raise ValueError(f"格式應為 provider/model，provider 僅支援：{', '.join(PROVIDER_NAMES)}")
    if provider != "local" and model not in model_catalog()[provider]:
        raise ValueError(f"{provider} 不支援 model：{model}（可用 /ai_models 查看）")
    return provider, model


class AIProviderPreferenceStore:
    """Persist the owner-defined ordered fallback chain of ``provider/model`` entries."""

    def __init__(self, path: str = AI_PROVIDER_FILE):
        self.path = path

    def get_policy(self) -> str:
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
                if isinstance(data, dict):
                    policy = data.get("policy")
                    if policy in AI_POLICIES:
                        return policy
                    if data.get("chain"):
                        return "custom"
        except (FileNotFoundError, ValueError):
            # Fall back to default policy if preferences file does not exist or is invalid
            pass
        return "auto"

    def set_policy(self, policy: str) -> List[str]:
        p = str(policy).strip().lower()
        if p not in AI_POLICIES:
            raise ValueError(f"不支援的策略：{policy}，可用策略：{', '.join(AI_POLICIES.keys())}")
        chain = build_strength_fallback_chain(p)
        self._save({"policy": p, "chain": chain})
        return chain

    def get_chain(self) -> List[str]:
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
        except (FileNotFoundError, ValueError):
            return default_chain()
        if not isinstance(data, dict):
            return default_chain()

        policy = data.get("policy")
        if policy in AI_POLICIES:
            return build_strength_fallback_chain(policy)

        chain = data.get("chain")
        if isinstance(chain, list):
            valid = []
            for entry in chain:
                try:
                    parse_entry(entry)
                except ValueError:
                    continue
                valid.append(entry)
            if valid:
                return valid

        # Legacy format: {"primary": "<provider>"} -> that provider's default model first.
        primary = data.get("primary")
        chain = default_chain()
        if primary in PROVIDER_NAMES:
            chain.sort(key=lambda entry: not entry.startswith(f"{primary}/"))
        return chain

    def set_chain(self, chain: List[str]) -> List[str]:
        normalized = []
        for entry in chain:
            provider, model = parse_entry(entry)
            item = f"{provider}/{model}"
            if item not in normalized:
                normalized.append(item)
        if not normalized:
            raise ValueError("至少需要一個 provider/model。")
        self._save({"policy": "custom", "chain": normalized})
        return normalized

    def add(self, entry: str, position: Optional[int] = None) -> List[str]:
        provider, model = parse_entry(entry)
        item = f"{provider}/{model}"
        chain = [existing for existing in self.get_chain() if existing != item]
        index = len(chain) if position is None else max(0, min(position - 1, len(chain)))
        chain.insert(index, item)
        return self.set_chain(chain)

    def remove(self, target: str) -> List[str]:
        """Remove by ``provider/model`` or by 1-based position."""
        chain = self.get_chain()
        if target.strip().isdigit():
            index = int(target) - 1
            if not 0 <= index < len(chain):
                raise ValueError(f"序號超出範圍：{target}")
            removed = chain.pop(index)
        else:
            provider, model = parse_entry(target)
            removed = f"{provider}/{model}"
            if removed not in chain:
                raise ValueError(f"清單中沒有 {removed}")
            chain.remove(removed)
        if not chain:
            raise ValueError(f"不能移除最後一個 provider/model（{removed}）。")
        return self.set_chain(chain)

    def get_local_base_url(self) -> str:
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
                if isinstance(data, dict) and data.get("local_base_url"):
                    return str(data["local_base_url"]).strip()
        except (FileNotFoundError, ValueError):
            # Fall back to environment variable or default when config file is missing or invalid
            pass
        return os.environ.get("LOCAL_AI_BASE_URL") or os.environ.get("LM_STUDIO_BASE_URL") or LOCAL_AI_BASE_URL

    def set_local_base_url(self, url: str) -> str:
        cleaned = str(url).strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("網址格式錯誤，必須以 http:// 或 https:// 開頭")
        self._save({"local_base_url": cleaned})
        return cleaned

    def _save(self, updates: Dict[str, Any]) -> None:
        current = {}
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                loaded = json.load(state_file)
                if isinstance(loaded, dict):
                    current = loaded
        except (FileNotFoundError, ValueError):
            current = {}
        current.update(updates)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp_path = f"{self.path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as state_file:
            json.dump(current, state_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)
