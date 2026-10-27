import json
import os
from typing import Optional

def _find_env_file() -> Optional[str]:
    candidate_paths = [
        os.path.join(os.getcwd(), ".env"),
        "/home/pi/gvw_screener/.env",
        os.path.expanduser("~/gvw_screener/.env"),
        os.path.expanduser("~/.hermes/.env"),
        "/home/pi/.hermes/.env",
        os.path.expanduser("~/.env")
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            return path
    return None

def load_env():
    env_file = _find_env_file()
    if env_file and os.path.exists(env_file):
        try:
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            # Ignore errors reading optional env file
            pass

def load_pi_agent_config(pi_dir: Optional[str] = None):
    """
    Auto-inherits API keys and local AI endpoint configurations from ~/.pi/agent/
    (created by `pi login` or pi configuration) if not already explicitly set in environment or .env.
    This establishes ~/.pi/agent as the single source of truth across gvw_bot and pi CLI.
    """
    target_dir = pi_dir or os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")

    # 1. auth.json (API keys for Google, OpenAI, etc.)
    auth_file = os.path.join(target_dir, "auth.json")
    if os.path.exists(auth_file):
        try:
            with open(auth_file, "r", encoding="utf-8") as f:
                auth_data = json.load(f)

            # Google AI Studio / Gemini key
            if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
                google_entry = auth_data.get("google", {})
                if isinstance(google_entry, dict) and google_entry.get("key"):
                    os.environ["GEMINI_API_KEY"] = google_entry["key"]
                elif isinstance(google_entry, str) and google_entry:
                    os.environ["GEMINI_API_KEY"] = google_entry

            # OpenAI key
            if not os.environ.get("OPENAI_API_KEY"):
                openai_entry = auth_data.get("openai", {})
                if isinstance(openai_entry, dict) and openai_entry.get("key"):
                    os.environ["OPENAI_API_KEY"] = openai_entry["key"]
                elif isinstance(openai_entry, str) and openai_entry:
                    os.environ["OPENAI_API_KEY"] = openai_entry
        except Exception:
            pass

    # 2. models.json (Local LLM providers / LM-Studio base URL, apiKey, etc.)
    models_file = os.path.join(target_dir, "models.json")
    if os.path.exists(models_file):
        try:
            with open(models_file, "r", encoding="utf-8") as f:
                models_data = json.load(f)
            providers = models_data.get("providers", {})
            local_provider = providers.get("local", {})
            if isinstance(local_provider, dict):
                if not os.environ.get("LOCAL_AI_BASE_URL") and not os.environ.get("LM_STUDIO_BASE_URL"):
                    base_url = local_provider.get("baseUrl")
                    if base_url:
                        os.environ["LOCAL_AI_BASE_URL"] = base_url
                if not os.environ.get("LOCAL_AI_API_KEY"):
                    api_key = local_provider.get("apiKey")
                    if api_key:
                        os.environ["LOCAL_AI_API_KEY"] = api_key
        except Exception:
            pass

# Initialize environment variables
load_env()
load_pi_agent_config()

# Base directories resolution
def _resolve_base_dir() -> str:
    # 1. Custom ENV override
    if os.environ.get("GVW_HOME"):
        return os.environ["GVW_HOME"]
    # 2. Raspberry Pi server environment
    if os.path.exists("/home/pi/gvw_screener"):
        return "/home/pi/gvw_screener"
    if os.path.exists("/home/pi/.hermes/gvw_screener"):
        return "/home/pi/.hermes/gvw_screener"
    # 3. Local repository workspace
    return os.getcwd()

BASE_DIR = _resolve_base_dir()

# Data directory
DATA_DIR = os.environ.get("GVW_DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

# State & Result files
UNIVERSE_FILE = os.environ.get("GVW_UNIVERSE_FILE", os.path.join(BASE_DIR, "universe.json"))
POINTER_FILE = os.environ.get("GVW_POINTER_FILE", os.path.join(BASE_DIR, "pointer.json"))
RESULT_FILE = os.environ.get("GVW_RESULT_FILE", os.path.join(BASE_DIR, "all_rank.json"))
HEALTH_STATUS_FILE = os.environ.get("GVW_HEALTH_FILE", os.path.join(BASE_DIR, "health_status.json"))
MARKET_INDEX_FILE = os.environ.get("GVW_MARKET_INDEX_FILE", os.path.join(BASE_DIR, "market_index.json"))
SIGNAL_CACHE_FILE = os.environ.get("GVW_SIGNAL_CACHE_FILE", os.path.join(BASE_DIR, "last_signals.json"))

# Logs directory
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
COLLECTOR_LOG_FILE = os.path.join(LOGS_DIR, "collector.log")

# TTL Settings (Hours)
TTL_HIGH_SCORE = 4    # Score > 0.9
TTL_MID_SCORE = 24    # Score > 0.6
TTL_LOW_SCORE = 168   # Others (7 days)
MAX_CONSECUTIVE_FAILURES = 3

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_HOME_CHANNEL = os.environ.get("TELEGRAM_HOME_CHANNEL", "")
TELEGRAM_OWNER_USER_ID = os.environ.get("TELEGRAM_OWNER_USER_ID", "")

# FinMind Configuration
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_TOKEN")

# Telegram access control
AI_ACCESS_FILE = os.environ.get("GVW_AI_ACCESS_FILE", os.path.join(DATA_DIR, "ai_access.json"))

# OpenAI Configuration
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Google Gemma (Google AI Studio, OpenAI-compatible endpoint) Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma-3-27b-it")

# Local AI / LM-Studio Configuration (LAN OpenAI-compatible endpoint)
LOCAL_AI_BASE_URL = (
    os.environ.get("LOCAL_AI_BASE_URL")
    or os.environ.get("LM_STUDIO_BASE_URL", "")
)
LOCAL_AI_API_KEY = os.environ.get("LOCAL_AI_API_KEY", "lm-studio")
LOCAL_AI_MODEL = (
    os.environ.get("LOCAL_AI_MODEL")
    or os.environ.get("LM_STUDIO_MODEL")
    or "active"
)

# Owner-selected primary AI provider ("openai" or "gemma"); the other is the fallback
AI_PROVIDER_FILE = os.environ.get("GVW_AI_PROVIDER_FILE", os.path.join(DATA_DIR, "ai_provider.json"))
