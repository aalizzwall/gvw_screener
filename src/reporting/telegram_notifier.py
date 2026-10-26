import logging
from typing import Optional
import requests
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_HOME_CHANNEL

logger = logging.getLogger(__name__)

def send_telegram_message(
    text: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None
) -> bool:
    """
    Sends message to Telegram channel/chat.
    """
    token = bot_token or TELEGRAM_BOT_TOKEN
    target_chat = chat_id or TELEGRAM_HOME_CHANNEL

    if not token or not target_chat:
        logger.warning("Telegram token or chat_id not configured. Message not sent.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        logger.info("Message successfully sent to Telegram.")
        return True
    except Exception as e:
        logger.error(f"Telegram API Error: {e}")
        return False


def send_telegram_photo(
    photo: bytes,
    caption: str = "",
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """Send a PNG image (bytes) to a Telegram chat."""
    token = bot_token or TELEGRAM_BOT_TOKEN
    target_chat = chat_id or TELEGRAM_HOME_CHANNEL
    if not token or not target_chat or not photo:
        return False
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        response = requests.post(
            url,
            data={"chat_id": target_chat, "caption": caption[:1000], "parse_mode": "HTML"},
            files={"photo": ("chart.png", photo, "image/png")},
            timeout=30,
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram sendPhoto Error: {e}")
        return False

