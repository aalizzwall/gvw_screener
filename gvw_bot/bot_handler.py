import os
import sys
import time
import logging
from typing import Optional, Dict, Any, List
import requests

# Ensure parent directory is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_HOME_CHANNEL
from src.reporting.report_engine import generate_daily_report
from src.analysis.ranker import run_ranking
from src.core.orchestrator import Orchestrator
from src.analysis.intraday import run_intraday_monitoring

logger = logging.getLogger("gvw.bot")

class TelegramBotHandler:
    """
    Handles Telegram Bot commands and interactions via long polling.
    """

    def __init__(self, token: Optional[str] = None, scheduler=None, heartbeat_mgr=None):
        self.token = token or TELEGRAM_BOT_TOKEN
        self.scheduler = scheduler
        self.hb = heartbeat_mgr
        self.last_update_id = 0
        self.running = False

    def send_message(self, chat_id: int | str, text: str, parse_mode: str = "HTML") -> bool:
        if not self.token:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        try:
            r = requests.post(url, json=payload, timeout=20)
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Error sending telegram message: {e}")
            return False

    def handle_command(self, chat_id: int | str, command: str, text: str):
        cmd = command.lower().strip()
        logger.info(f"Received Telegram command: {cmd} from chat {chat_id}")

        if cmd in ["/start", "/help"]:
            msg = (
                "🤖 <b>GVW Screener 智能選股機器人</b>\n\n"
                "本機器人已內建自動排程引擎，定時執行採集、選股與推播：\n"
                "• 08:30 晨間速報\n"
                "• 09:00-13:30 盤中突破即時監控 (每10分鐘)\n"
                "• 14:00 盤後全市場數據採集\n"
                "• 15:00 全局 GVW 評分排名\n"
                "• 15:30 盤後篩選總結日報\n\n"
                "<b>可用即時指令：</b>\n"
                "/report - 立即產製當前篩選日報\n"
                "/status - 查詢系統與排程運行狀態\n"
                "/monitor - 立即執行一次盤中信號掃描\n"
                "/collect - 手動觸發數據採集輪詢\n"
                "/rank - 手動觸發重新計算排名\n"
                "/jobs - 查看各排程執行時間\n"
            )
            self.send_message(chat_id, msg)

        elif cmd in ["/report", "/today"]:
            self.send_message(chat_id, "⏳ 正在生成最新篩選報告，請稍候...")
            report = generate_daily_report()
            self.send_message(chat_id, report)

        elif cmd == "/status":
            health_info = self.hb.check_health() if self.hb else {}
            data = health_info.get("data", {})
            uptime = data.get("uptime_seconds", 0)
            hrs = uptime // 3600
            mins = (uptime % 3600) // 60

            status_text = (
                f"📊 <b>GVW Bot 服務運行狀態</b>\n\n"
                f"• 狀態：<code>{'正常運行中 ✅' if health_info.get('healthy') else '異常 ⚠️'}</code>\n"
                f"• 程序 PID：<code>{data.get('pid', 'N/A')}</code>\n"
                f"• 運行時間：<code>{hrs}小時 {mins}分鐘</code>\n"
                f"• 最後心跳：<code>{data.get('last_heartbeat', 'N/A')}</code>\n\n"
                "💡 hermes-agent 可透過讀取 <code>gvw_bot/heartbeat.json</code> 進行監控。"
            )
            self.send_message(chat_id, status_text)

        elif cmd == "/jobs":
            if not self.scheduler:
                self.send_message(chat_id, "⚠️ 排程器未附加。")
                return

            lines = ["⏰ <b>GVW 內建排程任務清單</b>\n"]
            for job in self.scheduler.jobs:
                last = job.last_run.strftime('%m-%d %H:%M') if job.last_run else "尚未執行"
                trigger_info = job.daily_time if job.daily_time else f"每 {job.interval_minutes} 分鐘"
                lines.append(f"• <b>{job.name}</b>: {trigger_info} (最後執行: <code>{last}</code>)")
            self.send_message(chat_id, "\n".join(lines))

        elif cmd == "/monitor":
            self.send_message(chat_id, "🔍 正在掃描盤中 Top-20 標的即時信號...")
            alerts = run_intraday_monitoring()
            if alerts:
                for a in alerts:
                    self.send_message(chat_id, a)
            else:
                self.send_message(chat_id, "✅ 掃描完成：目前無新突破/蓄勢信號。")

        elif cmd == "/collect":
            self.send_message(chat_id, "🚀 已觸發數據採集輪詢...")
            orchestrator = Orchestrator()
            stats = orchestrator.run_collection_cycle(batch_ratio=0.1, force=True)
            self.send_message(chat_id, f"✅ 採集完成：{stats}")

        elif cmd == "/rank":
            self.send_message(chat_id, "📈 正在重新計算全市場排名...")
            res = run_ranking()
            count = len(res.get("data", []))
            self.send_message(chat_id, f"✅ 排名計算完成，已評估 {count} 檔標的。可輸入 /report 查看。")

        else:
            self.send_message(chat_id, "未知指令，請輸入 /help 查看說明。")

    def poll_once(self):
        if not self.token:
            return

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 20}

        try:
            r = requests.get(url, params=params, timeout=25)
            if r.status_code == 200:
                updates = r.json().get("result", [])
                for update in updates:
                    self.last_update_id = update.get("update_id", self.last_update_id)
                    message = update.get("message", {})
                    text = message.get("text", "").strip()
                    chat = message.get("chat", {})
                    chat_id = chat.get("id")

                    if text and chat_id and text.startswith("/"):
                        command = text.split()[0]
                        self.handle_command(chat_id, command, text)
        except Exception as e:
            logger.debug(f"Telegram polling error: {e}")

    def run_polling_loop(self):
        self.running = True
        logger.info("Telegram Bot polling started.")
        while self.running:
            try:
                self.poll_once()
            except Exception as e:
                logger.error(f"Error in poll loop: {e}")
                time.sleep(5)
            time.sleep(1)

