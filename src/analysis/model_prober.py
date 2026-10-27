import time
import logging
import requests
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("gvw.ai.prober")

# In-memory cache for probed models: cache_key -> (timestamp, is_success, [models], detail)
_PROBE_CACHE: Dict[str, Tuple[float, bool, List[str], str]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes

# Priority preference ordering for ranking discovered models
_MODEL_PREFERENCES: Dict[str, List[str]] = {
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ],
    "gemma": [
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-1.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-2.0-pro",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-exp",
        "gemma-2-27b-it",
        "gemma-2-9b-it",
        "gemma-2-2b-it",
        "gemma-3-27b-it",
        "gemma-3-12b-it",
        "gemma-3-4b-it",
    ],
    "local": [
        "active",
        "qwen/qwen3.5-9b",
        "deepseek/deepseek-r1-0528-qwen3-8b",
        "google/gemma-4-12b",
        "qwen-3.6-9b",
        "qwen2.5-7b",
        "llama-3.2-3b",
    ],
}

def _clean_model_id(model_id: str) -> str:
    m = str(model_id).strip()
    if m.startswith("models/"):
        m = m[len("models/"):]
    return m


_EXCLUDED_PATTERNS = {
    "openai": (
        "embedding", "audio", "realtime", "tts", "whisper",
        "dall-e", "moderation", "search", "transcription",
        "preview-realtime",
    ),
    "gemma": (
        "embedding", "aqa", "vision-preview", "imagen", "veo",
        "learnlm", "image", "tts", "whisper", "bimodal",
        "computer-use", "robotics",
    ),
    "local": (
        "embedding", "bge-", "rerank",
    ),
}


def _filter_and_rank_models(provider: str, discovered_ids: List[str]) -> List[str]:
    """Filters discovered models and ranks them by conversational suitability."""
    exclusions = _EXCLUDED_PATTERNS.get(provider, ())
    valid_ids = set()
    for mid in discovered_ids:
        mid_clean = _clean_model_id(mid)
        if not mid_clean or any(ex in mid_clean.lower() for ex in exclusions):
            continue

        if provider == "gemma":
            # ONLY keep conversational models: must start with gemini- or gemma-
            # Google endpoint returns many experimental/preview/system models like antigravity-preview
            if not (mid_clean.startswith("gemini-") or mid_clean.startswith("gemma-")):
                continue
            if "antigravity" in mid_clean.lower():
                continue

        if provider == "openai":
            # OpenAI conversational models must start with gpt-, o1, o3, chatgpt-
            if not (
                mid_clean.startswith("gpt-")
                or mid_clean.startswith("o1")
                or mid_clean.startswith("o3")
                or mid_clean.startswith("chatgpt-")
            ):
                continue

        valid_ids.add(mid_clean)

    ranked: List[str] = []
    # 1. Add known high-priority models in preference order if present in discovered IDs
    priorities = _MODEL_PREFERENCES.get(provider, [])
    for p in priorities:
        p_clean = _clean_model_id(p)
        if p_clean in valid_ids and p_clean not in ranked:
            ranked.append(p_clean)

    # 2. Add remaining valid discovered models (prioritize non-preview over preview)
    non_preview = [m for m in sorted(valid_ids) if "preview" not in m.lower() and m not in ranked]
    preview = [m for m in sorted(valid_ids) if "preview" in m.lower() and m not in ranked]
    for m in non_preview + preview:
        ranked.append(m)

    return ranked


def probe_available_models(
    provider: str,
    key: str,
    local_base_url: Optional[str] = None,
    timeout: int = 4,
    force_refresh: bool = False,
) -> Tuple[bool, List[str], str]:
    """
    Probes provider API to dynamically discover available models.
    Returns: (is_probe_successful, ranked_models_list, detail_message)
    """
    if not key and provider != "local":
        return False, [], "未設定 API 金鑰"

    cache_key = f"{provider}:{key[:8] if key else ''}:{local_base_url or ''}"
    now = time.time()
    if not force_refresh and cache_key in _PROBE_CACHE:
        cached_time, ok, cached_models, detail = _PROBE_CACHE[cache_key]
        if now - cached_time < CACHE_TTL_SECONDS:
            return ok, list(cached_models), detail

    discovered: List[str] = []
    try:
        if provider == "openai":
            url = "https://api.openai.com/v1/models"
            resp = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
            if resp.status_code == 401:
                res = (False, [], "HTTP 401 (認證失敗，金鑰無效)")
                _PROBE_CACHE[cache_key] = (now, res[0], res[1], res[2])
                return res
            resp.raise_for_status()
            data = resp.json().get("data", [])
            discovered = [
                _clean_model_id(m["id"])
                for m in data
                if isinstance(m, dict) and "id" in m
            ]

        elif provider == "gemma":
            # Attempt 1: Google OpenAI-compatible models endpoint
            url = "https://generativelanguage.googleapis.com/v1beta/openai/models"
            headers = {"Authorization": f"Bearer {key}"}
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                discovered = [
                    _clean_model_id(m["id"])
                    for m in data
                    if isinstance(m, dict) and "id" in m
                ]
            else:
                # Attempt 2: Google native models endpoint
                native_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                resp2 = requests.get(native_url, timeout=timeout)
                if resp2.status_code in (400, 401, 403):
                    res = (False, [], f"HTTP {resp2.status_code} (API Key 無效或權限不足)")
                    _PROBE_CACHE[cache_key] = (now, res[0], res[1], res[2])
                    return res
                resp2.raise_for_status()
                models_list = resp2.json().get("models", [])
                for m in models_list:
                    if isinstance(m, dict) and "name" in m:
                        name = _clean_model_id(m["name"])
                        discovered.append(name)

        elif provider == "local":
            base = (local_base_url or "").strip().rstrip("/")
            if not base:
                return False, ["active"], "未設定 LOCAL_AI_BASE_URL"
            if base.endswith("/chat/completions"):
                base = base[:-len("/chat/completions")]
            models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            resp = requests.get(models_url, timeout=timeout)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            discovered = [
                _clean_model_id(m["id"])
                for m in data
                if isinstance(m, dict) and "id" in m
            ]
            if not discovered:
                discovered = ["active"]

    except requests.exceptions.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else type(e).__name__
        logger.debug(f"Probing {provider} models failed: {detail} ({e})")
        res = (False, [], f"探測連線失敗 ({detail})")
        _PROBE_CACHE[cache_key] = (now, res[0], res[1], res[2])
        return res
    except Exception as e:
        logger.debug(f"Error parsing models for {provider}: {e}")
        res = (False, [], f"解析失敗: {type(e).__name__}")
        _PROBE_CACHE[cache_key] = (now, res[0], res[1], res[2])
        return res

    ranked = _filter_and_rank_models(provider, discovered)
    res = (True, ranked, f"探測成功 ({len(ranked)} 個可用模型)")
    _PROBE_CACHE[cache_key] = (now, res[0], res[1], res[2])
    return res


def get_intra_provider_pool(
    provider: str,
    key: str,
    local_base_url: Optional[str] = None,
    preferred_model: Optional[str] = None,
    max_models: int = 3,
    force_refresh: bool = False,
) -> List[str]:
    """
    Constructs an intra-provider model fallback pool (Tier 1).
    - If probe is successful: uses discovered models, prioritizing preferred_model if it exists.
      If preferred_model is NOT found in probed models (guessed/invalid), uses best probed models!
    - If probe is unsuccessful (offline/mock): uses preferred_model directly without guessing.
    """
    probe_ok, probed, _ = probe_available_models(
        provider, key, local_base_url=local_base_url, force_refresh=force_refresh
    )

    clean_pref = _clean_model_id(preferred_model or "")
    pool: List[str] = []

    if probe_ok and probed:
        # If preferred model is a generic alias like "active", "auto", "default", or empty,
        # use the probed models directly!
        if clean_pref in ("active", "auto", "default", ""):
            pool = probed[:max_models]
        elif clean_pref in probed:
            pool.append(clean_pref)
            for m in probed:
                if m != clean_pref and len(pool) < max_models:
                    pool.append(m)
        else:
            # Preferred model was not found in probed models (e.g. gemma-3-27b-it 404),
            # use top probed models directly!
            pool = probed[:max_models]
    else:
        # Probe unsuccessful or offline: use preferred model directly
        if clean_pref and clean_pref not in ("default", "auto"):
            pool.append(clean_pref)
        else:
            pool.append("default" if provider != "local" else "active")

    return pool
