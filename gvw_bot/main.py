#!/usr/bin/env python3
"""
GVW Bot & Autonomous Scheduler Service
Runs as a background daemon managing periodic collection, ranking, reporting,
intraday monitoring, and Telegram bot interactions.
"""
import os
import logging
import re
import signal
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

# Ensure project root is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from src.config import LOGS_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_USER_ID
from gvw_bot.heartbeat import HeartbeatManager
from gvw_bot.scheduler import GVWScheduler
from gvw_bot.bot_handler import TelegramBotHandler
from src.state.alert_subscribers import AlertSubscriptionStore


class CredentialRedactionFilter(logging.Filter):
    _patterns = (
        (
            re.compile(r"(https://api\.telegram\.org/bot)\d+:[A-Za-z0-9_-]+"),
            r"\1[REDACTED]",
        ),
        (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+"), r"\1[REDACTED]"),
        (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]+"), "[REDACTED]"),
    )

    def filter(self, record):
        message = record.getMessage()
        for pattern, replacement in self._patterns:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


def configure_logging():
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(
            os.path.join(LOGS_DIR, "gvw_bot.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(CredentialRedactionFilter())
    logging.basicConfig(level=logging.INFO, handlers=handlers)


configure_logging()
logger = logging.getLogger("gvw.bot.main")

def get_git_commit_hash() -> str:
    try:
        res = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            cwd=parent_dir,
            timeout=5,
        ).strip()
        return res[:7]
    except Exception:
        return "unknown"


def main():
    logger.info("==========================================")
    logger.info("   GVW Bot & Autonomous Service Starting   ")
    logger.info("==========================================")

    # 1. Initialize Heartbeat
    heartbeat_mgr = HeartbeatManager()
    heartbeat_mgr.write_heartbeat(status="starting")

    # 2. Start Autonomous Background Scheduler
    alert_store = AlertSubscriptionStore()
    scheduler = GVWScheduler(heartbeat_mgr=heartbeat_mgr, alert_store=alert_store)
    scheduler.start()

    # 3. Start Telegram Bot Interaction Handler
    bot = TelegramBotHandler(
        token=TELEGRAM_BOT_TOKEN,
        scheduler=scheduler,
        heartbeat_mgr=heartbeat_mgr,
        alert_store=alert_store,
    )

    # Graceful shutdown handler
    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}, shutting down gracefully...")
        scheduler.stop()
        bot.running = False
        heartbeat_mgr.write_heartbeat(status="stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    heartbeat_mgr.write_heartbeat(status="running")
    logger.info("Service initialized. Hermes-agent can monitor via heartbeat.json.")

    if TELEGRAM_BOT_TOKEN:
        if TELEGRAM_OWNER_USER_ID:
            commit_hash = get_git_commit_hash()
            try:
                bot.send_message(
                    TELEGRAM_OWNER_USER_ID,
                    f"🤖 <b>GVW Bot 服務已啟動並上線！</b>\n"
                    f"• 版本：<code>{commit_hash}</code>\n"
                    f"• 狀態：<code>正常運行中 ✅</code>",
                )
            except Exception as e:
                logger.warning(f"Failed to send startup notification: {e}")
        logger.info("Telegram Bot Token detected. Starting interactive bot polling...")
        bot.run_polling_loop()
    else:
        logger.warning("No TELEGRAM_BOT_TOKEN found. Running in Headless Scheduler Mode.")
        while True:
            time.sleep(10)

if __name__ == "__main__":
    main()
