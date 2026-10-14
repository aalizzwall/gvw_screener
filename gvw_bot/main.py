#!/usr/bin/env python3
"""
GVW Bot & Autonomous Scheduler Service
Runs as a background daemon managing periodic collection, ranking, reporting,
intraday monitoring, and Telegram bot interactions.
"""
import os
import sys
import time
import signal
import logging

# Ensure project root is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from src.config import TELEGRAM_BOT_TOKEN
from gvw_bot.heartbeat import HeartbeatManager
from gvw_bot.scheduler import GVWScheduler
from gvw_bot.bot_handler import TelegramBotHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("gvw.bot.main")

def main():
    logger.info("==========================================")
    logger.info("   GVW Bot & Autonomous Service Starting   ")
    logger.info("==========================================")

    # 1. Initialize Heartbeat
    heartbeat_mgr = HeartbeatManager()
    heartbeat_mgr.write_heartbeat(status="starting")

    # 2. Start Autonomous Background Scheduler
    scheduler = GVWScheduler(heartbeat_mgr=heartbeat_mgr)
    scheduler.start()

    # 3. Start Telegram Bot Interaction Handler
    bot = TelegramBotHandler(
        token=TELEGRAM_BOT_TOKEN,
        scheduler=scheduler,
        heartbeat_mgr=heartbeat_mgr
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
        logger.info("Telegram Bot Token detected. Starting interactive bot polling...")
        bot.run_polling_loop()
    else:
        logger.warning("No TELEGRAM_BOT_TOKEN found. Running in Headless Scheduler Mode.")
        while True:
            time.sleep(10)

if __name__ == "__main__":
    main()

