import os
import sys
import time
import json
import re
import logging
import html
import subprocess
import threading
from typing import Optional, Dict, Any, List, Union, Tuple
import requests

# Ensure parent directory is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from src.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_HOME_CHANNEL,
    TELEGRAM_OWNER_USER_ID,
)
from src.analysis.ai_analyst import analyze_stock, check_providers, _provider_key
from src.state.ai_access import AIAnalysisAccessStore
from src.state.ai_provider import (
    AIProviderPreferenceStore,
    model_catalog,
    AI_CHAIN_PRESETS,
    AI_POLICIES,
    parse_entry,
)
from src.reporting.report_engine import generate_daily_report, get_financials, get_suggested_price
from src.analysis.ranker import run_ranking
from src.core.orchestrator import Orchestrator
from src.analysis.intraday import run_intraday_monitoring
from src.diagnostics.doctor import GVWDoctor
from src.agent.issue_solver import IssueSolver, SecurityGuardrailError
from src.agent.maintenance_agent import MaintenanceAgent
from src.state.alert_subscribers import AlertSubscriptionStore
from src.state.feedback_queue import FeedbackQueueStore
from src.state.state_manager import StateManager

logger = logging.getLogger("gvw.bot")

BOT_COMMANDS = [
    {"command": "report", "description": "查看最新選股日報與全市場 Top 10"},
    {"command": "analyze", "description": "AI 個股深度評估（例如 /analyze 2330）"},
    {"command": "monitor", "description": "盤中即時突破信號掃描與推播開關"},
    {"command": "feedback", "description": "回報上則異常回覆內容供自我巡檢整理與修復"},
    {"command": "maintain", "description": "切換維護模式，自然語言診斷與修復"},
    {"command": "status", "description": "系統服務狀態、排程任務與健康管理"},
    {"command": "ai", "description": "AI 智慧控制中心（模型順序、健康檢測、地端設定）"},
    {"command": "update", "description": "一鍵從 GitHub 更新並重啟服務"},
    {"command": "help", "description": "查看完整使用指南與指令說明"},
]

PUBLIC_COMMANDS = [
    {"command": "start", "description": "開始使用 / 叫出功能按鈕"},
    {"command": "report", "description": "查看最新選股日報與 Top 10"},
    {"command": "analyze", "description": "個股深度 AI 評估（需申請）"},
    {"command": "feedback", "description": "回報上則異常回覆內容"},
    {"command": "help", "description": "查看使用指南與指令說明"},
]

MENU_BUTTONS = {
    "🏆 Top 10 選股": "/report",
    "📊 選股報告 / Top 10": "/report",
    "🔔 盤中監控 (on)": "/toggle_monitor",
    "🔕 盤中監控 (off)": "/toggle_monitor",
    "🔔 盤中監控": "/toggle_monitor",
    "🔕 盤中監控": "/toggle_monitor",
    "🤖 AI 個股分析": "/analyze",
    "📝 申請 AI 分析": "/request_ai",
    "⚠️ 問題回報": "/feedback",
    "🩺 系統狀態": "/status",
    "🔍 盤中監控": "/monitor",
    "📥 採集資料": "/collect",
    "🏆 重新排名": "/rank",
    "⏰ 排程任務": "/jobs",
}
REPLY_KEYBOARD = {
    "keyboard": [
        ["🏆 Top 10 選股", "🔕 盤中監控"],
        ["🤖 AI 個股分析", "⚠️ 問題回報"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
PUBLIC_REPLY_KEYBOARD = {
    "keyboard": [
        ["🏆 Top 10 選股", "🔕 盤中監控"],
        ["📝 申請 AI 分析", "⚠️ 問題回報"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def format_telegram_html(text: str) -> str:
    """Format markdown text safely into Telegram HTML while preserving code blocks and escaping special chars."""
    if not text:
        return ""
    s = str(text).strip()

    # Unwrap JSON if text was somehow wrapped in raw action JSON
    if s.startswith("{") and ("\"action\"" in s or "\"final_answer\"" in s or "\"answer\"" in s):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict) and "answer" in parsed:
                s = str(parsed["answer"]).strip()
        except Exception:
            m = re.search(r'"(?:answer|final_answer)"\s*:\s*"([\s\S]*?)"\s*\}', s)
            if m:
                ans_raw = m.group(1)
                try:
                    s = json.loads(f'"{ans_raw}"').strip()
                except Exception:
                    s = ans_raw.replace('\\n', '\n').replace('\\"', '"').strip()

    code_blocks = []
    inline_codes = []

    def save_code_block(match):
        code = match.group(1)
        idx = len(code_blocks)
        escaped_code = html.escape(code.strip())
        code_blocks.append(f"<pre><code>{escaped_code}</code></pre>")
        return f"___CODE_BLOCK_{idx}___"

    def save_inline_code(match):
        code = match.group(1)
        idx = len(inline_codes)
        escaped_code = html.escape(code)
        inline_codes.append(f"<code>{escaped_code}</code>")
        return f"___INLINE_CODE_{idx}___"

    # Multi-line or single-line code block: ```[lang]\n...\n``` or ```...```
    s = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?([\s\S]*?)```", save_code_block, s)

    # Inline code: `code`
    s = re.sub(r"`([^`\n]+)`", save_inline_code, s)

    # Escape HTML special chars in prose (&, <, >)
    s = html.escape(s)

    # Markdown Bold: **bold**
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)

    # Restore placeholders
    for idx, replacement in enumerate(inline_codes):
        s = s.replace(f"___INLINE_CODE_{idx}___", replacement)
    for idx, replacement in enumerate(code_blocks):
        s = s.replace(f"___CODE_BLOCK_{idx}___", replacement)

    return s


class TelegramBotHandler:
    """
    Handles Telegram Bot commands and interactions via long polling.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        scheduler=None,
        heartbeat_mgr=None,
        owner_user_id: Optional[str] = None,
        ai_access_store: Optional[AIAnalysisAccessStore] = None,
        ai_provider_store: Optional[AIProviderPreferenceStore] = None,
        alert_store: Optional[AlertSubscriptionStore] = None,
        feedback_store: Optional[FeedbackQueueStore] = None,
        doctor: Optional[GVWDoctor] = None,
        issue_solver: Optional[IssueSolver] = None,
        maintenance_agent: Optional[MaintenanceAgent] = None,
    ):
        self.token = token or TELEGRAM_BOT_TOKEN
        self.scheduler = scheduler
        self.hb = heartbeat_mgr
        configured_owner = TELEGRAM_OWNER_USER_ID if owner_user_id is None else owner_user_id
        self.owner_user_id = str(configured_owner).strip()
        self.ai_access_store = ai_access_store or AIAnalysisAccessStore()
        self.ai_provider_store = ai_provider_store or AIProviderPreferenceStore()
        self.alert_store = alert_store or AlertSubscriptionStore()
        self.feedback_store = feedback_store or FeedbackQueueStore()
        self.doctor = doctor or GVWDoctor(ai_provider_store=self.ai_provider_store)
        self.issue_solver = issue_solver or IssueSolver(ai_provider_store=self.ai_provider_store)
        self.maintenance_agent = maintenance_agent or MaintenanceAgent(
            ai_provider_store=self.ai_provider_store,
            doctor=self.doctor,
            scheduler=self.scheduler,
        )
        self.last_update_id = 0
        self.running = False
        self._updating = False
        self._fixing = False
        self.maintenance_mode_chats: set = set()
        self.pending_analysis_chats: Dict[Union[int, str], str] = {}
        self.pending_feedback_note_chats: Dict[Union[int, str], int] = {}
        self.last_bot_replies: Dict[Union[int, str], str] = {}
        self.last_user_inputs: Dict[Union[int, str], str] = {}

    def send_message(
        self,
        chat_id: Union[int, str],
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        user_id: Optional[Union[int, str]] = None,
        track_reply: bool = True,
    ) -> bool:
        if track_reply and text and not text.startswith(("⏳", "🔍 正在", "🔄 正在", "📝 <b>已收到您的問題回報")):
            self.last_bot_replies[chat_id] = text
        if not self.token:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"

        # Telegram has a 4096 character limit per message; safely chunk if needed
        chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)] if len(text) > 3800 else [text]
        success = True
        for idx, chunk in enumerate(chunks):
            markup = (
                (reply_markup if reply_markup is not None else self._keyboard_for_chat(chat_id, user_id=user_id))
                if idx == len(chunks) - 1
                else None
            )
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "reply_markup": markup,
            }
            try:
                r = requests.post(url, json=payload, timeout=20)
                if r.status_code != 200:
                    logger.warning(f"Telegram sendMessage error ({r.status_code}): {r.text}")
                    # If HTML parsing failed due to invalid tags, retry once with plain text
                    if parse_mode == "HTML" and "can't parse entities" in str(r.text).lower():
                        payload["parse_mode"] = ""
                        r_retry = requests.post(url, json=payload, timeout=20)
                        if r_retry.status_code == 200:
                            continue
                    success = False
            except Exception as e:
                logger.error(f"Error sending telegram message: {e}")
                success = False
        return success

    def send_photo(
        self,
        chat_id: Union[int, str],
        photo: bytes,
        caption: str = "",
    ) -> bool:
        if not self.token or not photo:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        try:
            r = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1000], "parse_mode": "HTML"},
                files={"photo": ("chart.png", photo, "image/png")},
                timeout=30,
            )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Error sending telegram photo: {e}")
            return False

    def _send_trade_chart(self, chat_id: Union[int, str], ticker: str, name: str, stock_data: Any):
        """Best-effort: never let chart failures break the text reply."""
        try:
            from src.reporting.chart_generator import render_trade_chart
            prices = stock_data.get("prices", []) if isinstance(stock_data, dict) else []
            png = render_trade_chart(ticker, name, prices)
            if png:
                self.send_photo(
                    chat_id, png,
                    f"{html.escape(str(name))} ({ticker}) 買賣情境圖\n僅供參考，非投資建議。",
                )
        except Exception as exc:
            logger.warning("Trade chart failed for %s: %s", ticker, exc)

    def _keyboard_for_chat(
        self,
        chat_id: Union[int, str],
        user_id: Optional[Union[int, str]] = None,
    ) -> Dict[str, Any]:
        target_uid = user_id if user_id is not None else chat_id
        is_sub = self.alert_store.is_subscribed(chat_id)
        monitor_btn = "🔔 盤中監控" if is_sub else "🔕 盤中監控"
        can_analyze = self._can_analyze(target_uid)
        ai_btn = "🤖 AI 個股分析" if can_analyze else "📝 申請 AI 分析"

        return {
            "keyboard": [
                ["🏆 Top 10 選股", monitor_btn],
                [ai_btn, "⚠️ 問題回報"],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    def handle_command(
        self,
        chat_id: Union[int, str],
        command: str,
        text: str,
        user_id: Optional[Union[int, str]] = None,
        chat_type: str = "private",
        user: Optional[Dict[str, Any]] = None,
    ):
        cmd = MENU_BUTTONS.get(command.strip(), command.lower().strip())
        logger.info(f"Received Telegram command: {cmd} from chat {chat_id}")
        if cmd != "/feedback":
            self.pending_feedback_note_chats.pop(chat_id, None)
            self.last_user_inputs[chat_id] = text.strip()
        owner_only_commands = {
            "/status", "/jobs", "/monitor",
            "/collect", "/rank", "/ai_requests",
            "/ai", "/ai_status", "/ai_models", "/ai_set", "/ai_add", "/ai_remove", "/ai_check",
            "/ai_local", "/update", "/doctor", "/fix", "/maintain", "/audit", "/check_github",
        }
        if cmd in owner_only_commands and not self._is_owner(user_id):
            self.send_message(chat_id, "⛔ 此功能僅限系統負責人使用。", user_id=user_id)
            return

        if cmd in ["/start", "/help"]:
            is_owner = self._is_owner(user_id)
            msg = (
                "🤖 <b>GVW Screener 智能選股機器人</b>\n\n"
                "本機器人內建自主排程引擎，定時執行採集、選股與推播：\n"
                "• 08:30 晨間速報\n"
                "• 09:00-13:30 盤中突破即時監控 (每10分鐘)\n"
                "• 14:00 盤後全市場數據採集\n"
                "• 15:00 全局 GVW 評分排名\n"
                "• 15:30 盤後篩選總結日報\n\n"
                "<b>📈 核心功能（可直接點擊下方鍵盤按鈕）：</b>\n"
                "• 🏆 <b>Top 10 選股</b> (<code>/report</code>)：查看最新全市場 GVW Top 10 核心優質標的\n"
                "• 🔔 <b>盤中監控</b> (<code>/monitor</code>)：即時掃描信號或開關推播通知\n"
                "• 🤖 <b>AI 個股分析</b> (<code>/analyze 2330</code>)：基本面、動能與價量 AI 深度評估\n"
            )
            if is_owner:
                msg += (
                    "\n🛠️ <b>系統管理與運維指令（負責人專用）：</b>\n"
                    "• <code>/maintain</code>：切換系統維護模式（自然語言自主排查主機、日誌與資料庫）\n"
                    "• <code>/status</code>：服務運行狀態、排程任務管理與健康診斷面板\n"
                    "• <code>/ai</code>：AI 智慧控制中心（視覺化調整模型順序、健康檢測、地端設定）\n"
                    "• <code>/update</code>：一鍵從 GitHub 拉取最新代碼並重啟服務\n"
                )
            self.send_message(chat_id, msg, user_id=user_id)

        elif cmd in ["/report", "/today", "/top10"]:
            self.send_message(chat_id, "⏳ 正在生成最新篩選報告，請稍候...", user_id=user_id)
            report = generate_daily_report()
            self.send_message(chat_id, report, user_id=user_id)

        elif cmd == "/toggle_monitor":
            is_sub = self.alert_store.is_subscribed(chat_id)
            if is_sub:
                self.alert_store.unsubscribe(chat_id)
                self.send_message(
                    chat_id,
                    "🔕 <b>已關閉盤中監控主動推播</b>\n\n您將不會再收到盤中突破通知。若欲重新開啟，請點擊下方按鈕。",
                    user_id=user_id,
                )
            else:
                self.alert_store.subscribe(chat_id)
                self.send_message(
                    chat_id,
                    "🔔 <b>已開啟盤中監控主動推播</b>\n\n當全市場 Top 30 核心標的同時觸發「分時三步法」突破信號時，系統將主動發送推播通知您！",
                    user_id=user_id,
                )

        elif cmd == "/status":
            health_info = self.hb.check_health() if self.hb else {}
            data = health_info.get("data", {})
            uptime = data.get("uptime_seconds", 0)
            hrs = uptime // 3600
            mins = (uptime % 3600) // 60

            scheduler_summary = "未載入"
            if self.scheduler and hasattr(self.scheduler, "jobs"):
                job_count = len(self.scheduler.jobs)
                running_jobs = sum(1 for j in self.scheduler.jobs if getattr(j, "is_running", False))
                scheduler_summary = f"{job_count} 項任務 (執行中: {running_jobs})"

            status_text = (
                f"📊 <b>GVW Bot 服務運行狀態</b>\n\n"
                f"• 服務狀態：<code>{'正常運行中 ✅' if health_info.get('healthy') else '異常 ⚠️'}</code>\n"
                f"• 程序 PID：<code>{data.get('pid', 'N/A')}</code>\n"
                f"• 運行時間：<code>{hrs}小時 {mins}分鐘</code>\n"
                f"• 最後心跳：<code>{data.get('last_heartbeat', 'N/A')}</code>\n"
                f"• 定時排程：<code>{scheduler_summary}</code>\n\n"
                "👇 <b>快捷管理操作：</b>"
            )
            buttons = [
                [
                    {"text": "⏰ 查看詳細排程", "callback_data": "status_jobs"},
                    {"text": "🩺 執行健康診斷", "callback_data": "status_doctor"},
                ],
                [
                    {"text": "📥 手動採集資料", "callback_data": "status_collect"},
                    {"text": "📈 重新計算排名", "callback_data": "status_rank"},
                ],
            ]
            self.send_message(
                chat_id,
                status_text,
                reply_markup={"inline_keyboard": buttons},
                user_id=user_id,
            )

        elif cmd == "/jobs":
            if not self.scheduler:
                self.send_message(chat_id, "⚠️ 排程器未附加。")
                return

            lines = ["⏰ <b>GVW 內建排程任務清單</b>\n"]
            for job in self.scheduler.jobs:
                last = job.last_run.strftime('%m-%d %H:%M') if job.last_run else "尚未執行"
                trigger_info = job.daily_time if job.daily_time else f"每 {job.interval_minutes} 分鐘"
                status_suffix = " (執行中 ⏳)" if getattr(job, "is_running", False) else ""
                lines.append(f"• <b>{job.name}</b>: {trigger_info} (最後執行: <code>{last}</code>){status_suffix}")
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

        elif cmd in {"/analyze", "/request_ai"}:
            if chat_type != "private":
                self.send_message(chat_id, "請在與 Bot 的私訊中申請或使用 AI 分析，避免個人資料公開於群組。")
                return
            if cmd == "/request_ai" or not self._can_analyze(user_id):
                self._request_ai_access(chat_id, user_id, user or {})
                return
            parts = text.split(maxsplit=1) if text.startswith("/") else []
            if len(parts) < 2:
                self.pending_analysis_chats[chat_id] = str(user_id or "")
                self.send_message(chat_id, "請輸入 4 至 6 位數的台股代碼進行 AI 分析，例如 2330。")
                return
            self._send_stock_analysis(chat_id, parts[1].strip())

        elif cmd == "/feedback":
            note_arg = text.split(maxsplit=1)[1].strip() if text.startswith("/") and len(text.split(maxsplit=1)) > 1 else ""
            last_reply = self.last_bot_replies.get(chat_id, "")
            last_input = self.last_user_inputs.get(chat_id, "")
            if not last_reply and not note_arg:
                self.send_message(
                    chat_id,
                    "ℹ️ 目前尚無上一則機器人回覆記錄可引用；您可直接輸入 <code>/feedback 問題描述</code> 回報問題。",
                    user_id=user_id,
                    track_reply=False,
                )
                return

            entry = self.feedback_store.add_report(
                chat_id=chat_id,
                user_id=user_id if user_id is not None else chat_id,
                bot_reply=last_reply,
                user_input=last_input,
                note=note_arg,
            )
            snippet = last_reply.strip().replace("\n", " ")[:80] if last_reply else "（無前則回覆）"
            self.send_message(
                chat_id,
                f"📝 <b>已收到您的問題回報（編號 #{entry['id']}）</b>\n\n"
                f"• <b>已引用前則回覆</b>：<code>{html.escape(snippet)}...</code>\n"
                + (f"• <b>補充說明</b>：<code>{html.escape(note_arg)}</code>\n" if note_arg else "")
                + "\n系統將於<b>自我巡檢</b>時自動彙整此回報，並評估是否可直接自動修復（如重抓缺漏資料、切換備援模型）或建立 GitHub Issue 追蹤修復。",
                user_id=user_id,
                track_reply=False,
            )

        elif cmd in {"/query", "/stock"}:
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip().isdigit():
                self.send_message(chat_id, "請輸入要查找的台股代碼，例如：<code>/query 2330</code>", user_id=user_id)
                return
            self._send_stock_lookup(chat_id, parts[1].strip(), user_id=user_id)

        elif cmd == "/ai_requests":
            requests_list = self.ai_access_store.pending_requests()
            if not requests_list:
                self.send_message(chat_id, "目前沒有待審核的 AI 使用申請。")
                return
            for request in requests_list:
                self._notify_owner_of_request(request)

        elif cmd in {"/ai", "/ai_control", "/ai_dashboard"}:
            args = text.split()[1:]
            if not args or args[0].lower() in {"status", "info", "dashboard"}:
                text_out, buttons = self._render_ai_dashboard()
                self.send_message(
                    chat_id,
                    text_out,
                    reply_markup={"inline_keyboard": buttons},
                    user_id=user_id,
                )
            elif args[0].lower() in AI_POLICIES:
                policy = args[0].lower()
                self.ai_provider_store.set_policy(policy)
                policy_title = AI_POLICIES[policy]["title"]
                text_out, buttons = self._render_ai_dashboard()
                self.send_message(
                    chat_id,
                    f"✅ <b>已套用策略：{policy_title}</b>\n\n{text_out}",
                    reply_markup={"inline_keyboard": buttons},
                    user_id=user_id,
                )
            elif args[0].lower() == "check":
                self.send_message(chat_id, "🩺 正在檢測 AI 模型可用性，請稍候...")
                self.send_message(chat_id, self._run_ai_check())
            elif args[0].lower() == "models":
                self._render_ai_models(chat_id)
            elif args[0].lower() == "local":
                if len(args) > 1:
                    new_url = self.ai_provider_store.set_local_base_url(args[1])
                    self.send_message(chat_id, f"✅ 地端 LM-Studio 端點已更新為：<code>{html.escape(new_url)}</code>")
                else:
                    self.handle_command(chat_id, "/ai_local", "/ai_local", user_id=user_id)
            elif args[0].lower() == "set":
                self.send_message(chat_id, self._update_ai_chain("/ai_set", args[1:]))
            else:
                text_out, buttons = self._render_ai_dashboard()
                self.send_message(
                    chat_id,
                    text_out,
                    reply_markup={"inline_keyboard": buttons},
                    user_id=user_id,
                )

        elif cmd == "/ai_status":
            self.send_message(chat_id, self._ai_status_text())

        elif cmd == "/ai_models":
            self._render_ai_models(chat_id)

        elif cmd in {"/ai_set", "/ai_add", "/ai_remove"}:
            args = text.split()[1:]
            self.send_message(chat_id, self._update_ai_chain(cmd, args))

        elif cmd == "/ai_check":
            self.send_message(chat_id, "🩺 正在檢測 AI 模型可用性，請稍候...")
            self.send_message(chat_id, self._run_ai_check())

        elif cmd == "/ai_local":
            args = text.split()[1:]
            if not args:
                endpoint = self.ai_provider_store.get_local_base_url() or "未設定"
                self.send_message(
                    chat_id,
                    f"🏠 <b>地端 LM-Studio 端點</b>\n\n"
                    f"• 目前設定：<code>{html.escape(endpoint)}</code>\n\n"
                    f"如需更換請輸入：<code>/ai_local &lt;URL&gt;</code>\n"
                    f"例如：<code>/ai_local http://192.168.1.100:1234/v1</code>",
                )
            else:
                try:
                    new_url = self.ai_provider_store.set_local_base_url(args[0])
                    self.send_message(
                        chat_id,
                        f"✅ 地端 LM-Studio 端點已更新為：<code>{html.escape(new_url)}</code>\n\n"
                        f"可輸入 /ai_check 檢測連線狀況。",
                    )
                except ValueError as exc:
                    self.send_message(chat_id, f"⚠️ {html.escape(str(exc))}")

        elif cmd == "/update":
            if self._updating:
                self.send_message(chat_id, "⚠️ 系統正在執行更新程序中，請稍候...")
            else:
                self._updating = True
                threading.Thread(
                    target=self._execute_system_update,
                    args=(chat_id,),
                    daemon=True,
                    name="GVWSystemUpdateThread",
                ).start()

        elif cmd == "/doctor":
            args = text.split()[1:]
            if args and args[0].lower() == "fix":
                if len(args) > 1 and (args[1].startswith("#") or args[1].isdigit()):
                    issue_id = args[1]
                    self._trigger_issue_solver(chat_id, issue_id)
                else:
                    self.send_message(chat_id, "🔧 正在執行系統常規自愈維護 (日誌輪替/空間壓縮/AI備援切換)...")
                    try:
                        actions = self.doctor.run_remediation()
                        resp = "🛠️ <b>系統自愈維護執行結果：</b>\n\n" + "\n".join([f"• {act}" for act in actions])
                        self.send_message(chat_id, resp)
                    except Exception as e:
                        self.send_message(chat_id, f"❌ 自愈程序異常：<code>{html.escape(str(e))}</code>")
            else:
                self.send_message(chat_id, "🩺 正在執行全系統健康診斷...")
                try:
                    report = self.doctor.format_diagnostics_report()
                    self.send_message(chat_id, report)
                except Exception as e:
                    self.send_message(chat_id, f"❌ 診斷失敗：<code>{html.escape(str(e))}</code>")

        elif cmd == "/fix":
            args = text.split()[1:]
            if not args or not (args[0].startswith("#") or args[0].isdigit()):
                self.send_message(
                    chat_id,
                    "💡 請提供要修復的 GitHub Issue 編號，例如：<code>/fix #28</code> 或 <code>/doctor fix #28</code>",
                )
            else:
                issue_id = args[0]
                self._trigger_issue_solver(chat_id, issue_id)

        elif cmd in ["/audit", "/check_github"]:
            self.send_message(chat_id, "🔍 正在執行 GitHub 與系統全面巡檢，請稍候...")
            threading.Thread(
                target=self._execute_github_audit,
                args=(chat_id,),
                daemon=True,
                name="GVWAuditThread",
            ).start()

        elif cmd == "/maintain":
            if chat_id in self.maintenance_mode_chats:
                self.maintenance_mode_chats.remove(chat_id)
                self.maintenance_agent.clear_session(str(chat_id))
                self.send_message(
                    chat_id,
                    "🚪 <b>已退出【系統維護模式】</b>，返回正常選股與推播模式。",
                )
            else:
                self.maintenance_mode_chats.add(chat_id)
                self.send_message(
                    chat_id,
                    "🛠️ <b>已進入【系統維護模式】</b>\n\n"
                    "維護 Agent 採 Pi 架構：擁有 bash / read / write / edit 四大原語，可自主探索並排查整台主機。\n"
                    "⚠️ 它能執行指令與修改檔案（已限制：逾時 60 秒、拒絕破壞性指令、遮蔽金鑰、不可寫入 .env）。\n"
                    "在此模式下，您可以直接傳送任何自然語言問題，例如：\n"
                    "• <i>為什麼 PB PE 股利 是空白的？</i>\n"
                    "• <i>那 2330 呢？幫我看目前資料庫中的狀態</i>\n"
                    "• <i>檢查最近排程執行狀況與是否有異常日誌？</i>\n\n"
                    "👉 常用指令：\n"
                    "• <code>reset</code> 或 <code>/reset</code>：清除對話記憶\n"
                    "• <code>exit</code> 或 <code>/maintain</code>：隨時退出此模式",
                )

        elif chat_id in self.maintenance_mode_chats:
            user_msg = text.strip()
            if user_msg.lower() in ["/exit", "exit", "退出", "離開"]:
                self.maintenance_mode_chats.remove(chat_id)
                self.maintenance_agent.clear_session(str(chat_id))
                self.send_message(
                    chat_id,
                    "🚪 <b>已退出【系統維護模式】</b>，返回正常選股與推播模式。",
                )
            elif user_msg.lower() in ["/reset", "reset", "/clear", "clear", "重置", "清除"]:
                self.maintenance_agent.clear_session(str(chat_id))
                self.send_message(
                    chat_id,
                    "🔄 <b>已重置維護對話記憶</b>，您可以開始新的問題排查。",
                )
            else:
                self.send_message(chat_id, "🕵️‍♂️ 正在分析您的問題並調用診斷工具檢視系統狀態...")
                threading.Thread(
                    target=self._handle_maintenance_query,
                    args=(chat_id, user_msg),
                    daemon=True,
                    name=f"GVWMaintenanceThread-{chat_id}",
                ).start()

        elif self.pending_analysis_chats.get(chat_id) == str(user_id or ""):
            ticker = text.strip()
            if not ticker.isdigit() or not 4 <= len(ticker) <= 6:
                self.send_message(chat_id, "請輸入 4 至 6 位數的台股代碼，例如 2330；或輸入 /help 查看功能。")
                return
            self.pending_analysis_chats.pop(chat_id, None)
            self._send_stock_analysis(chat_id, ticker)

        elif text.strip().isdigit() and 4 <= len(text.strip()) <= 6:
            ticker = text.strip()
            self._send_stock_lookup(chat_id, ticker, user_id=user_id)

        else:
            self.send_message(
                chat_id,
                "👋 <b>您好！歡迎使用 GVW Screener 智能選股機器人</b>\n\n"
                "• 請直接輸入 <b>4 至 6 位數台股代碼</b>（例如 <code>2330</code>）查找最新行情、估值與建議買入區間。\n"
                "• 點擊下方功能按鈕：\n"
                "  - 🏆 <b>Top 10 選股</b>：查看最新核心優質標的\n"
                "  - 🔔 <b>盤中監控</b>：開啟/關閉即時突破通知\n"
                "  - 🤖 <b>AI 個股分析</b>：深度多空 AI 評估\n\n"
                "輸入 /help 可查看完整指令說明。",
                user_id=user_id,
            )

    def _render_ai_models(self, chat_id: Union[int, str]):
        from src.analysis.model_prober import probe_available_models, _clean_model_id
        local_url = self.ai_provider_store.get_local_base_url()
        lines = ["📚 <b>可選的 AI Provider / Model 清單</b>\n"]
        buttons = []

        catalog = model_catalog()

        # 1. Local LM-Studio models
        lines.append("🏠 <b>LM-Studio (地端模型)</b>")
        ok, local_probed, detail = probe_available_models("local", "", local_base_url=local_url)
        if ok and local_probed:
            lines.append(f"• 狀態：探測成功（發現 {len(local_probed)} 個模型）")
            lines.append("• <code>local/active</code>（自動使用當前載入模型，推薦）")
            buttons.append([{"text": "🏠 切換至 local/active", "callback_data": "ai_select:local/active"}])
            for m in local_probed:
                clean_m = _clean_model_id(m)
                if clean_m not in ("active", ""):
                    lines.append(f"• <code>local/{clean_m}</code>")
                    short_name = clean_m.split("/")[-1] if "/" in clean_m else clean_m
                    if len(buttons) < 7:
                        buttons.append([{"text": f"🏠 切換至 {short_name[:28]}", "callback_data": f"ai_select:local/{clean_m}"}])
        else:
            lines.append(f"• 狀態：{detail}")
            lines.extend(f"• <code>local/{m}</code>" for m in catalog.get("local", ["active"]))

        # 2. Google Gemma / Gemini models
        lines.append("\n🌐 <b>Google Gemma / Gemini</b>")
        gemini_key = _provider_key("gemma")
        if gemini_key:
            ok, g_probed, detail = probe_available_models("gemma", gemini_key)
            if ok and g_probed:
                lines.append(f"• 狀態：探測成功（發現 {len(g_probed)} 個可用對話模型）")
                for m in g_probed[:8]:
                    lines.append(f"• <code>gemma/{m}</code>")
                    if len(buttons) < 11:
                        buttons.append([{"text": f"🌐 切換至 gemma/{m}", "callback_data": f"ai_select:gemma/{m}"}])
            else:
                lines.append(f"• 狀態：{detail}")
                lines.extend(f"• <code>gemma/{m}</code>" for m in catalog.get("gemma", [])[:25])
        else:
            lines.append("• 狀態：未設定 GEMINI_API_KEY")
            lines.extend(f"• <code>gemma/{m}</code>" for m in catalog.get("gemma", [])[:25])

        # 3. OpenAI models
        lines.append("\n⚡ <b>OpenAI</b>")
        oa_key = _provider_key("openai")
        if oa_key:
            ok, oa_probed, detail = probe_available_models("openai", oa_key)
            if ok and oa_probed:
                lines.append(f"• 狀態：探測成功（發現 {len(oa_probed)} 個模型）")
                for m in oa_probed[:5]:
                    lines.append(f"• <code>openai/{m}</code>")
            else:
                lines.append(f"• 狀態：{detail}")
                lines.extend(f"• <code>openai/{m}</code>" for m in catalog.get("openai", [])[:4])
        else:
            lines.append("• 狀態：未設定 OPENAI_API_KEY")
            lines.extend(f"• <code>openai/{m}</code>" for m in catalog.get("openai", [])[:4])

        # 4. Ready-to-copy sequence templates for /ai_set
        current_chain = self.ai_provider_store.get_chain()
        current_cmd = f"/ai_set {' '.join(current_chain)}" if current_chain else "/ai_set gemma/gemini-2.5-flash-lite local/active"

        lines.append("\n📋 <b>一鍵複製 /ai_set 模型順序模板（點擊代碼即可複製）：</b>")
        for key, preset in AI_CHAIN_PRESETS.items():
            chain_str = " ".join(preset["chain"])
            lines.append(f"• <b>{preset['title']}</b>（{preset['description']}）：")
            lines.append(f"<code>/ai_set {html.escape(chain_str)}</code>")

        lines.append(f"• <b>當前配置複製（方便手動編輯順序）：</b>")
        lines.append(f"<code>{html.escape(current_cmd)}</code>")

        lines.append("\n💡 <b>切換模型與順序方式：</b>")
        lines.append("1. 點擊下方【一鍵套用】按鈕，直接配置整組階梯備援鏈")
        lines.append("2. 點擊單一模型按鈕，快速將該模型提升為第 1 順位主力")
        lines.append("3. 或直接點擊上方 <code>/ai_set ...</code> 代碼複製後發送自訂順序")

        # Preset buttons placed at the top for quick access
        preset_buttons = [
            [{"text": f"⚡ 套用：{AI_CHAIN_PRESETS['lite_ladder']['title']}", "callback_data": "ai_preset:lite_ladder"}],
            [{"text": f"🧠 套用：{AI_CHAIN_PRESETS['standard']['title']}", "callback_data": "ai_preset:standard"}],
            [{"text": f"🏠 套用：{AI_CHAIN_PRESETS['local_first']['title']}", "callback_data": "ai_preset:local_first"}],
        ]
        all_buttons = preset_buttons + buttons

        reply_markup = {"inline_keyboard": all_buttons} if all_buttons else None
        self.send_message(chat_id, "\n".join(lines), reply_markup=reply_markup)

    def _render_ai_dashboard(self) -> Tuple[str, List[List[Dict[str, str]]]]:
        chain = self.ai_provider_store.get_chain()
        policy = self.ai_provider_store.get_policy()
        policy_meta = AI_POLICIES.get(
            policy,
            {"title": "🛠️ 自訂備援順序", "description": "手動設定備援順序 (/ai_set)"},
        )
        local_url = self.ai_provider_store.get_local_base_url()

        lines = [
            "🎛️ <b>【GVW AI 智慧控制中心】</b>",
            f"• <b>當前策略</b>：{policy_meta['title']}",
            f"• <i>{policy_meta['description']}</i>\n",
            "📡 <b>Provider 配置狀態：</b>",
        ]

        gemini_key = _provider_key("gemma")
        oa_key = _provider_key("openai")

        lines.append(f"• 🌐 <b>Google Gemini</b>：{'✅ API Key 已配置' if gemini_key else '⚠️ 未設定 GEMINI_API_KEY'}")
        lines.append(f"• ⚡ <b>OpenAI</b>：{'✅ API Key 已配置' if oa_key else '⚠️ 未設定 OPENAI_API_KEY'}")
        lines.append(f"• 🏠 <b>Local (LM-Studio)</b>：<code>{html.escape(local_url or '未設定')}</code>\n")

        lines.append("🪜 <b>目前生效備援梯隊（依模型強度自動排序，地端保底）：</b>")
        for idx, entry in enumerate(chain, start=1):
            prov, model = parse_entry(entry)
            icon = "🌐" if prov == "gemma" else ("⚡" if prov == "openai" else "🏠")
            if idx == 1:
                role = "主力"
            elif idx == len(chain):
                role = "地端終極保底" if prov == "local" else f"備援 {idx-1}"
            else:
                role = f"備援 {idx-1}"
            lines.append(f"{idx}. {icon} <code>{html.escape(entry)}</code> ({role})")

        lines.append("\n💡 <i>點擊下方按鈕可一鍵切換策略，系統自動依最新模型強度重排最佳梯隊，無需手動打指令。</i>")

        buttons = [
            [
                {"text": "⚡ 自動強度階梯 (推薦)", "callback_data": "ai_policy:auto"},
                {"text": "🌐 Gemini 優先", "callback_data": "ai_policy:gemini"},
            ],
            [
                {"text": "⚡ OpenAI 優先", "callback_data": "ai_policy:openai"},
                {"text": "🏠 地端 Active 優先", "callback_data": "ai_policy:local"},
            ],
            [
                {"text": "🩺 立即連線健康檢測", "callback_data": "ai_check:run"},
                {"text": "🔄 刷新控制面板", "callback_data": "ai_policy:refresh"},
            ],
        ]
        return "\n".join(lines), buttons

    def _ai_status_text(self) -> str:
        lines = ["🤖 <b>AI provider/model 順序</b>"]
        for index, entry in enumerate(self.ai_provider_store.get_chain(), start=1):
            tag = "（主力）" if index == 1 else ""
            if entry.startswith("local/"):
                endpoint = self.ai_provider_store.get_local_base_url()
                lines.append(f"{index}. <code>{html.escape(entry)}</code>{tag} (<code>{html.escape(endpoint)}</code>)")
            else:
                lines.append(f"{index}. <code>{html.escape(entry)}</code>{tag}")
        lines.append("\n前一個失敗（如 429）時，自動改用下一個。")
        return "\n".join(lines)

    def _update_ai_chain(self, cmd: str, args: List[str]) -> str:
        usage = {
            "/ai_set": "用法：/ai_set &lt;provider/model&gt; [&lt;provider/model&gt; ...]\n"
                       "例如：/ai_set openai/gpt-4o gemma/gemma-3-27b-it",
            "/ai_add": "用法：/ai_add &lt;provider/model&gt; [位置]\n例如：/ai_add gemma/gemma-3-12b-it 2",
            "/ai_remove": "用法：/ai_remove &lt;provider/model|序號&gt;",
        }[cmd]
        if not args:
            return usage
        try:
            if cmd == "/ai_set":
                self.ai_provider_store.set_chain(args)
            elif cmd == "/ai_add":
                if len(args) > 2 or (len(args) == 2 and not args[1].isdigit()):
                    return usage
                self.ai_provider_store.add(args[0], int(args[1]) if len(args) == 2 else None)
            else:
                if len(args) != 1:
                    return usage
                self.ai_provider_store.remove(args[0])
        except ValueError as exc:
            return f"⚠️ {html.escape(str(exc))}"
        return "✅ 已更新。\n\n" + self._ai_status_text()

    def _run_ai_check(self) -> str:
        results = check_providers(self.ai_provider_store.get_chain())
        lines = ["🩺 <b>AI 模型檢測結果</b>"]
        for result in results:
            icon = "✅" if result["ok"] else "❌"
            entry = result.get("entry", "")
            detail = result.get("detail", "")
            label = result.get("label", "")
            header = (
                f"{html.escape(label)} (<code>{html.escape(entry)}</code>)"
                if label
                else f"<code>{html.escape(entry)}</code>"
            )
            lines.append(f"\n{icon} {header}：{html.escape(detail)}")

            probe_info = result.get("probe_status")
            if probe_info:
                lines.append(f"   • 模型探測：{html.escape(probe_info)}")

            tier1 = result.get("tier1_results")
            if tier1:
                lines.append("   • 第一層 (內部模型備援)：")
                for idx, t in enumerate(tier1, start=1):
                    t_icon = "🟢" if t["ok"] else "🔴"
                    role = "主力" if idx == 1 else f"備援 {idx-1}"
                    lines.append(
                        f"     {t_icon} {role} <code>{html.escape(t['model'])}</code>：{html.escape(t['detail'])}"
                    )

        healthy = [r["entry"] for r in results if r["ok"]]
        if not healthy:
            lines.append("\n⚠️ 所有 AI 模型目前皆不可用，維持原順序。")
        elif not results[0]["ok"]:
            unhealthy = [r["entry"] for r in results if not r["ok"]]
            self.ai_provider_store.set_chain(healthy + unhealthy)
            lines.append(f"\n🔀 主力不可用，已將可用者排前面，新主力：<code>{html.escape(healthy[0])}</code>")
        else:
            current_chain = self.ai_provider_store.get_chain()
            new_chain = [r["entry"] for r in results]
            if new_chain != current_chain:
                self.ai_provider_store.set_chain(new_chain)
        return "\n".join(lines)

    def _execute_system_update(self, chat_id: Union[int, str]):
        """Runs git fetch/pull, updates dependencies if needed, and triggers service restart."""
        try:
            self.send_message(chat_id, "🔄 正在檢查並拉取 GitHub 最新代碼...")

            # 1. git fetch origin main
            fetch_res = subprocess.run(
                ["git", "fetch", "origin", "main"],
                capture_output=True,
                text=True,
                timeout=45,
                cwd=parent_dir,
            )
            if fetch_res.returncode != 0:
                self.send_message(
                    chat_id,
                    f"❌ Git fetch 失敗：\n<code>{html.escape(fetch_res.stderr.strip())}</code>",
                )
                return

            local_rev = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                text=True,
                cwd=parent_dir,
            ).strip()
            remote_rev = subprocess.check_output(
                ["git", "rev-parse", "origin/main"],
                text=True,
                cwd=parent_dir,
            ).strip()

            if local_rev == remote_rev:
                short_hash = local_rev[:7]
                self.send_message(
                    chat_id,
                    f"✅ <b>目前已是最新版本！</b>\n"
                    f"• 當前 Commit：<code>{short_hash}</code>\n"
                    f"無須進行更新。",
                )
                return

            log_diff = subprocess.check_output(
                ["git", "log", f"{local_rev}..{remote_rev}", "--oneline"],
                text=True,
                cwd=parent_dir,
            ).strip()

            pull_res = subprocess.run(
                ["git", "pull", "origin", "main"],
                capture_output=True,
                text=True,
                timeout=45,
                cwd=parent_dir,
            )
            if pull_res.returncode != 0:
                self.send_message(
                    chat_id,
                    f"❌ Git pull 失敗：\n<code>{html.escape(pull_res.stderr.strip())}</code>",
                )
                return

            new_rev = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                text=True,
                cwd=parent_dir,
            ).strip()[:7]

            msg = (
                f"🚀 <b>系統代碼更新成功！</b>\n\n"
                f"• 版本變更：<code>{local_rev[:7]}</code> ➔ <code>{new_rev}</code>\n"
                f"• 更新內容：\n<code>{html.escape(log_diff)}</code>\n\n"
                f"正在重啟 <code>gvw_bot</code> 服務..."
            )
            self.send_message(chat_id, msg)

            req_file = os.path.join(parent_dir, "requirements.txt")
            if os.path.exists(req_file):
                python_candidates = [
                    sys.executable,
                    os.path.join(parent_dir, ".venv", "bin", "python"),
                    os.path.join(parent_dir, ".venv", "bin", "python3"),
                ]
                py_bin = next((p for p in python_candidates if p and os.path.exists(p)), None)
                if py_bin:
                    logger.info(f"Updating dependencies via {py_bin} -m pip...")
                    try:
                        subprocess.run(
                            [py_bin, "-m", "pip", "install", "-r", req_file],
                            timeout=90,
                            cwd=parent_dir,
                            capture_output=True,
                            text=True,
                        )
                    except Exception as pip_err:
                        logger.warning(f"Dependency installation skipped or failed: {pip_err}")

            time.sleep(1.5)

            logger.info("Restarting gvw_bot service via systemctl...")
            try:
                subprocess.Popen(["systemctl", "--user", "restart", "gvw_bot"])
            except Exception as se:
                logger.warning(f"Could not trigger systemctl restart: {se}")
                self.send_message(
                    chat_id,
                    f"⚠️ 代碼已更新，但自動重啟觸發失敗：<code>{html.escape(str(se))}</code>，請手動重啟。",
                )

        except Exception as e:
            logger.error(f"Error during system update: {e}", exc_info=True)
            self.send_message(chat_id, f"❌ 更新程序異常：<code>{html.escape(str(e))}</code>")
        finally:
            self._updating = False

    def _execute_github_audit(self, chat_id: Union[int, str]) -> None:
        """Runs full GitHub audit on-demand and reports results to Telegram."""
        from src.diagnostics.github_auditor import GitHubAuditor

        auditor = GitHubAuditor()
        try:
            result = auditor.run_full_audit()
            summary = result.get("summary", {})
            alerts_count = summary.get("alerts_count", 0)
            failures_count = summary.get("failures_count", 0)
            issues_count = summary.get("open_issues_count", 0)

            if result.get("has_problems"):
                created, issue_url = auditor.create_or_update_weekly_issue(result)
                action_text = "已建立新 Issue" if created else "已更新既有 Issue"
                msg = (
                    "⚠️ <b>【GitHub 系統巡檢完成】</b>\n"
                    f"發現異常項目（{action_text}）：\n"
                    f"• 🛡️ CodeQL 弱點警報: <b>{alerts_count}</b> 項\n"
                    f"• ❌ 過去 7 天 CI 失敗: <b>{failures_count}</b> 次\n"
                    f"• 📌 既有待跟進 Issue: <b>{issues_count}</b> 項\n\n"
                    f"🔗 <b>GitHub Issue 追蹤連結：</b>\n"
                    f"{issue_url}"
                )
            else:
                msg = (
                    "✅ <b>【GitHub 系統巡檢完成】</b>\n"
                    "專案健康狀態優良，未發現任何異常：\n"
                    f"• 🛡️ CodeQL 弱點警報: 0 項\n"
                    f"• ⚙️ CI/CD 執行狀態: 全部正常 (0 失敗)\n"
                    f"• 📌 待處理 Issue 追蹤: {issues_count} 項\n\n"
                    "無需建立 GitHub Issue。"
                )
            self.send_message(chat_id, msg)
        except Exception as e:
            logger.error(f"Error during manual GitHub audit: {e}", exc_info=True)
            self.send_message(chat_id, f"❌ 巡檢執行失敗：{html.escape(str(e))}")

    def _trigger_issue_solver(self, chat_id: Union[int, str], issue_id: str):
        """Dispatches IssueSolver in a daemon thread and sends updates to Telegram."""
        if self._fixing:
            self.send_message(chat_id, "⚠️ 目前已有自主修復任務正在進行中，請稍候...")
            return

        clean_id = issue_id.lstrip("#").strip()
        self._fixing = True
        self.send_message(
            chat_id,
            f"🤖 <b>[GVW Doctor Agent] 自主修復任務已啟動！</b>\n\n"
            f"• 目標：<code>Issue #{clean_id}</code>\n"
            f"• 流程：檢索錯誤 ➔ 沙盒隔離 ➔ AI 規劃 ➔ 單元測試 ➔ 提 PR\n"
            f"正在分析處理中，請稍候...",
        )

        def _worker():
            try:
                res = self.issue_solver.solve_issue(clean_id)
                if res.get("success"):
                    modified = ", ".join(res.get("modified_files", [])) or "無"
                    msg = (
                        f"🎉 <b>[GVW Doctor Agent] Issue #{clean_id} 自主修復成功！</b>\n\n"
                        f"• 標題：{html.escape(res.get('title', ''))}\n"
                        f"• 變更檔案：<code>{html.escape(modified)}</code>\n"
                        f"• 修復摘要：{html.escape(res.get('explanation', ''))}\n"
                        f"• 單元測試：100% 通過 ✅\n\n"
                        f"🚀 <b>已建立 Pull Request：</b>\n"
                        f"{res.get('pr_url')}\n\n"
                        f"請在 GitHub 審閱合併；合併後於此處輸入 /update 即可部署上線！"
                    )
                else:
                    msg = f"ℹ️ {res.get('message', '未能完成修復。')}"
                self.send_message(chat_id, msg)
            except SecurityGuardrailError as sge:
                logger.warning(f"Security guardrail triggered during issue solve: {sge}")
                self.send_message(
                    chat_id,
                    f"🛡️ <b>[安全護欄攔截]</b> 修復遭到阻斷：\n<code>{html.escape(str(sge))}</code>\n\n"
                    f"系統已確保未修改任何受保護檔案，此問題請人工介入處理。",
                )
            except Exception as e:
                logger.error(f"Error solving issue #{clean_id}: {e}", exc_info=True)
                self.send_message(
                    chat_id,
                    f"❌ <b>Issue #{clean_id} 自主修復失敗：</b>\n<code>{html.escape(str(e))}</code>",
                )
            finally:
                self._fixing = False

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"GVWIssueSolverThread-{clean_id}",
        ).start()

    def _handle_maintenance_query(self, chat_id: Union[int, str], question: str):
        try:
            answer = self.maintenance_agent.ask(question, session_id=str(chat_id))
            formatted_answer = format_telegram_html(answer)
            self.send_message(chat_id, f"🛠️ <b>【維護診斷報告】</b>\n\n{formatted_answer}")
        except Exception as e:
            logger.error(f"Maintenance agent query error: {e}", exc_info=True)
            self.send_message(chat_id, f"❌ 診斷過程發生異常：<code>{html.escape(str(e))}</code>")

    def _is_owner(self, user_id: Optional[Union[int, str]]) -> bool:
        return bool(self.owner_user_id and str(user_id) == self.owner_user_id)

    def _can_analyze(self, user_id: Optional[Union[int, str]]) -> bool:
        return self._is_owner(user_id) or self.ai_access_store.is_approved(user_id)

    def _request_ai_access(
        self,
        chat_id: Union[int, str],
        user_id: Optional[Union[int, str]],
        user: Dict[str, Any],
    ):
        if user_id is None:
            self.send_message(chat_id, "無法驗證 Telegram 使用者，請重新私訊 Bot。")
            return
        if self._is_owner(user_id):
            self.send_message(chat_id, "✅ 你是系統負責人，已可使用 AI 分析。")
            return
        if not self.owner_user_id:
            self.send_message(chat_id, "系統尚未設定負責人，暫時無法受理 AI 使用申請。")
            return

        status = self.ai_access_store.request(
            user_id,
            chat_id,
            str(user.get("username", "")),
            str(user.get("first_name", "")),
            str(user.get("last_name", "")),
        )
        if status == "approved":
            self.send_message(chat_id, "✅ 你已獲准使用 AI 分析，請輸入 /analyze 2330 開始。")
        elif status == "pending":
            self.send_message(chat_id, "🕒 你的 AI 分析使用申請仍在等待負責人審核。")
        else:
            request = self.ai_access_store.get_request(user_id)
            notified = bool(
                request and self._notify_owner_of_request({"user_id": str(user_id), **request})
            )
            self.send_message(
                chat_id,
                "🕒 已送出 AI 分析使用申請，待系統負責人核准或拒絕。"
                if notified
                else "⚠️ 申請已保存，但無法通知負責人；請稍後重試，負責人可用 /ai_requests 查詢。",
            )

    def _notify_owner_of_request(self, request: Dict[str, Any]) -> bool:
        user_id = str(request["user_id"])
        display_name = " ".join(
            part for part in (request.get("first_name"), request.get("last_name")) if part
        ) or "未提供姓名"
        username = request.get("username")
        if username:
            display_name += f" (@{username})"
        text = (
            "📝 <b>AI 個股分析使用申請</b>\n"
            f"申請人：{html.escape(display_name)}\n"
            f"Telegram user ID：<code>{html.escape(user_id)}</code>"
        )
        return self.send_message(
            self.owner_user_id,
            text,
            reply_markup={
                "inline_keyboard": [[
                    {"text": "✅ 允許", "callback_data": f"ai_access:approve:{user_id}"},
                    {"text": "❌ 拒絕", "callback_data": f"ai_access:reject:{user_id}"},
                ]]
            },
        )

    def handle_callback_query(self, callback: Dict[str, Any]):
        callback_id = callback.get("id")
        actor_id = callback.get("from", {}).get("id")
        data = str(callback.get("data", ""))

        # 1. Non-owner accessible callbacks
        if data.startswith("ai_analyze:"):
            ticker = data[len("ai_analyze:"):].strip()
            self._answer_callback_query(callback_id, f"正在分析 {ticker}...")
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            if self._can_analyze(actor_id):
                self._send_stock_analysis(chat_id, ticker)
            else:
                self._request_ai_access(chat_id, actor_id, callback.get("from", {}))
            return

        if data.startswith("ai_request_access"):
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            self._answer_callback_query(callback_id, "已送出 AI 申請")
            self._request_ai_access(chat_id, actor_id, callback.get("from", {}))
            return

        # 2. Owner-only admin callbacks
        if not self._is_owner(actor_id):
            self._answer_callback_query(callback_id, "僅限系統負責人操作。")
            return

        if data in ("tune:apply", "tune:ignore"):
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            pilot = getattr(self.scheduler, "autopilot", None)
            if pilot is None:
                self._answer_callback_query(callback_id, "自主管理模組未載入。")
                return
            if data == "tune:apply":
                weights = pilot.apply_proposal()
                if weights:
                    self._answer_callback_query(callback_id, "已套用新權重")
                    self.send_message(
                        chat_id,
                        "✅ <b>已套用新選股權重</b>（下次排名生效）\n"
                        + "\n".join(f"• {k}：{v:.0%}" for k, v in weights.items()),
                    )
                else:
                    self._answer_callback_query(callback_id, "建議已失效或不存在。")
            else:
                pilot.dismiss_proposal()
                self._answer_callback_query(callback_id, "已忽略本週建議")
            return

        if data == "status_jobs":
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            self._answer_callback_query(callback_id, "正在載入排程清單...")
            self.handle_command(chat_id, "/jobs", "/jobs", user_id=actor_id)
            return

        if data == "status_doctor":
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            self._answer_callback_query(callback_id, "正在執行健康診斷...")
            self.handle_command(chat_id, "/doctor", "/doctor", user_id=actor_id)
            return

        if data == "status_collect":
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            self._answer_callback_query(callback_id, "已觸發數據採集...")
            self.handle_command(chat_id, "/collect", "/collect", user_id=actor_id)
            return

        if data == "status_rank":
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            self._answer_callback_query(callback_id, "正在重新計算排名...")
            self.handle_command(chat_id, "/rank", "/rank", user_id=actor_id)
            return

        if data.startswith("ai_policy:"):
            policy_key = data[len("ai_policy:"):]
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            if policy_key == "refresh":
                self._answer_callback_query(callback_id, "已刷新控制中心。")
                text_out, buttons = self._render_ai_dashboard()
                self.send_message(chat_id, text_out, reply_markup={"inline_keyboard": buttons})
                return
            if policy_key not in AI_POLICIES:
                self._answer_callback_query(callback_id, "未知的策略。")
                return
            try:
                self.ai_provider_store.set_policy(policy_key)
                meta = AI_POLICIES[policy_key]
                self._answer_callback_query(callback_id, f"已套用：{meta['title']}")
                text_out, buttons = self._render_ai_dashboard()
                self.send_message(
                    chat_id,
                    f"✅ <b>已套用策略：{html.escape(meta['title'])}</b>\n\n{text_out}",
                    reply_markup={"inline_keyboard": buttons},
                )
            except Exception as e:
                self._answer_callback_query(callback_id, f"切換失敗: {e}")
            return

        if data == "ai_check:run":
            chat_id = callback.get("message", {}).get("chat", {}).get("id") or actor_id
            self._answer_callback_query(callback_id, "正在進行健康檢測...")
            self.send_message(chat_id, "🩺 正在檢測 AI 模型可用性，請稍候...")
            self.send_message(chat_id, self._run_ai_check())
            return

        if data.startswith("ai_preset:"):
            preset_key = data[len("ai_preset:"):]
            preset = AI_CHAIN_PRESETS.get(preset_key)
            if not preset:
                self._answer_callback_query(callback_id, "未知的推薦配置。")
                return
            try:
                self.ai_provider_store.set_chain(preset["chain"])
                self._answer_callback_query(callback_id, f"已套用：{preset['title']}")
                self.send_message(
                    actor_id,
                    f"✅ <b>已套用推薦模型順序：{html.escape(preset['title'])}</b>\n"
                    f"<i>{html.escape(preset['description'])}</i>\n\n"
                    + self._ai_status_text(),
                )
            except Exception as e:
                self._answer_callback_query(callback_id, f"套用失敗: {e}")
            return

        if data.startswith("ai_select:"):
            entry = data[len("ai_select:"):]
            try:
                current_chain = self.ai_provider_store.get_chain()
                new_chain = [entry] + [e for e in current_chain if e != entry]
                self.ai_provider_store.set_chain(new_chain)
                self._answer_callback_query(callback_id, f"已切換主力為 {entry}")
                self.send_message(
                    actor_id,
                    f"✅ <b>已將主力 AI 模型切換為：</b><code>{html.escape(entry)}</code>\n\n"
                    + self._ai_status_text(),
                )
            except Exception as e:
                self._answer_callback_query(callback_id, f"切換失敗: {e}")
            return

        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "ai_access" or parts[1] not in {"approve", "reject"}:
            self._answer_callback_query(callback_id, "無效的審核操作。")
            return
        user_id = parts[2]
        approved = parts[1] == "approve"
        request = self.ai_access_store.get_request(user_id)
        if not self.ai_access_store.decide(user_id, approved):
            self._answer_callback_query(callback_id, "申請已處理或不存在。")
            return

        decision = "已核准" if approved else "已拒絕"
        self._answer_callback_query(callback_id, decision)
        if request:
            applicant_message = (
                f"✅ 你的 AI 分析使用申請{decision}，可輸入 /analyze 2330 開始。"
                if approved
                else "❌ 你的 AI 分析使用申請已被拒絕；如有需要可再次申請。"
            )
            self.send_message(request["chat_id"], applicant_message, user_id=user_id)
        self.send_message(self.owner_user_id, f"已處理 user ID {html.escape(user_id)}：{decision}。")

    def _answer_callback_query(self, callback_id: Optional[str], text: str):
        if not self.token or not callback_id:
            return
        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=20,
        )
        response.raise_for_status()

    def _send_stock_analysis(self, chat_id: Union[int, str], ticker: str):
        self.send_message(chat_id, f"⏳ 正在準備資料並分析 {ticker}，請稍候...")
        try:
            state = StateManager()
            stock_data = state.get_stock_data(ticker)
            need_refresh = False
            if not stock_data or not stock_data.get("prices"):
                need_refresh = True
            else:
                basic = stock_data.get("basic_info", {})
                if not isinstance(basic, dict) or (float(basic.get("pb", 0) or 0) == 0 and float(basic.get("yield", 0) or 0) == 0):
                    need_refresh = True

            if need_refresh:
                try:
                    from src.core.orchestrator import Orchestrator
                    orchestrator = Orchestrator(state_manager=state)
                    orchestrator.update_stock(ticker, force=True)
                except Exception as exc:
                    logger.warning("Auto-refresh before AI analysis failed for %s: %s", ticker, exc)

            report = analyze_stock(ticker)
        except (RuntimeError, ValueError) as exc:
            self.send_message(chat_id, str(exc))
            return

        disclaimer = "AI 分析僅供參考，非投資建議。"
        if disclaimer not in report:
            report = f"{report}\n\n{disclaimer}"
        self.send_message(
            chat_id,
            f"🤖 <b>{ticker} 個股 AI 評估</b>\n\n{format_telegram_html(report)}",
        )

    def _send_stock_lookup(
        self,
        chat_id: Union[int, str],
        ticker: str,
        user_id: Optional[Union[int, str]] = None,
    ):
        self.send_message(chat_id, f"🔍 正在查詢 {ticker} 的行情與財務估值...", user_id=user_id)
        state = StateManager()
        stock_data = state.get_stock_data(ticker)

        if not stock_data or not stock_data.get("prices"):
            try:
                orchestrator = Orchestrator(state_manager=state)
                orchestrator.update_stock(ticker, force=True)
                stock_data = state.get_stock_data(ticker)
            except Exception as exc:
                logger.warning("Auto-fetch on lookup failed for %s: %s", ticker, exc)

        basic_info = stock_data.get("basic_info", {}) if isinstance(stock_data, dict) else {}
        name = (
            stock_data.get("name")
            or basic_info.get("name")
            or basic_info.get("stock_name")
            or basic_info.get("公司名稱")
            or ticker
        )

        fin = get_financials(ticker, state_manager=state)
        price_range = get_suggested_price(ticker, state_manager=state)

        prices = stock_data.get("prices", []) if isinstance(stock_data, dict) else []
        latest_price_str = "N/A"
        date_str = ""
        if prices and isinstance(prices[-1], dict):
            latest = prices[-1]
            close_val = latest.get("close")
            if close_val is not None:
                try:
                    latest_price_str = f"{float(close_val):,.2f} 元"
                except (ValueError, TypeError):
                    latest_price_str = f"{close_val} 元"
            date_str = str(latest.get("date", ""))

        ranks = state.get_ranks().get("data", [])
        rank_row = next((r for r in ranks if str(r.get("ticker", "")) == ticker), None)
        status_str = "一般標的"
        if rank_row:
            score = rank_row.get("final_score", 0)
            entry_sig = rank_row.get("entry_signal", "")
            if entry_sig:
                status_str = f"{entry_sig} (評分: {score:.2f})"
            else:
                status_str = f"評分: {score:.2f}"

        pe_val = fin.get("pe", 0)
        pe_display = f"{pe_val}" if pe_val > 0 else ("N/A (虧損)" if fin.get("is_loss") or fin.get("pb", 0) > 0 else "0.0")

        lines = [
            f"📊 <b>{html.escape(name)} ({ticker}) 個股行情與財務估值</b>\n",
            f"• <b>最新收盤</b>：<code>{latest_price_str}</code>" + (f" ({date_str})" if date_str else ""),
            f"• <b>建議買入區間</b>：<code>{price_range}</code>",
            f"• <b>GVW 狀態</b>：<code>{status_str}</code>\n",
            "📊 <b>核心財務與評價指標：</b>",
            f"• <b>ROE</b>：<code>{fin.get('roe', 0)}%</code>",
            f"• <b>本益比 (P/E)</b>：<code>{pe_display}</code>",
            f"• <b>股價淨值比 (P/B)</b>：<code>{fin.get('pb', 0)}</code>",
            f"• <b>股利殖利率</b>：<code>{fin.get('yield', 0)}%</code>\n",
        ]

        self._send_trade_chart(chat_id, ticker, name, stock_data)

        can_analyze = self._can_analyze(user_id or chat_id)
        if can_analyze:
            lines.append("💡 點擊下方按鈕可直接啟動 <b>AI 深度多空評估</b>：")
            btn = [[{"text": f"🤖 執行 {name} AI 評估", "callback_data": f"ai_analyze:{ticker}"}]]
        else:
            lines.append("💡 如需 AI 深度多空邏輯評估，請點擊下方按鈕提出申請：")
            btn = [[{"text": "📝 申請 AI 分析權限", "callback_data": f"ai_request_access:{user_id or chat_id}"}]]

        self.send_message(
            chat_id,
            "\n".join(lines),
            user_id=user_id,
            reply_markup={"inline_keyboard": btn},
        )

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
                    callback = update.get("callback_query")
                    if callback:
                        self.handle_callback_query(callback)
                        continue
                    message = update.get("message", {})
                    text = message.get("text", "").strip()
                    chat = message.get("chat", {})
                    chat_id = chat.get("id")
                    sender = message.get("from", {})
                    user_id = sender.get("id")

                    chat_type = chat.get("type", "private")
                    is_private = chat_type == "private"
                    if text and chat_id and (
                        is_private
                        or text.startswith("/")
                        or text in MENU_BUTTONS
                        or self.pending_analysis_chats.get(chat_id) == str(user_id or "")
                        or chat_id in self.maintenance_mode_chats
                    ):
                        command = text.split()[0] if text.startswith("/") else text
                        self.handle_command(
                            chat_id,
                            command,
                            text,
                            user_id=user_id,
                            chat_type=chat_type,
                            user=sender,
                        )
        except Exception as e:
            logger.debug(f"Telegram polling error: {e}")

    def register_commands(self):
        if not self.token:
            return

        # 1. Clear / delete commands for regular users (public default scope has NO menu, uses keyboard)
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/deleteMyCommands",
                json={"scope": {"type": "default"}},
                timeout=20,
            )
            requests.post(
                f"https://api.telegram.org/bot{self.token}/setChatMenuButton",
                json={"menu_button": {"type": "default"}},
                timeout=20,
            )
        except Exception as e:
            logger.warning(f"Could not clear public commands menu: {e}")

        # 2. Register full command menu exclusively for Owner chat
        if self.owner_user_id:
            try:
                owner_id_int = int(self.owner_user_id)
                response = requests.post(
                    f"https://api.telegram.org/bot{self.token}/setMyCommands",
                    json={
                        "commands": BOT_COMMANDS,
                        "scope": {"type": "chat", "chat_id": owner_id_int},
                    },
                    timeout=20,
                )
                response.raise_for_status()
                if not response.json().get("ok"):
                    raise RuntimeError("Telegram rejected owner command registration")

                requests.post(
                    f"https://api.telegram.org/bot{self.token}/setChatMenuButton",
                    json={
                        "chat_id": owner_id_int,
                        "menu_button": {"type": "commands"},
                    },
                    timeout=20,
                )
            except Exception as e:
                logger.error(f"Failed to register owner menu: {e}")

    def run_polling_loop(self):
        self.running = True
        try:
            self.register_commands()
        except (requests.RequestException, ValueError, RuntimeError):
            logger.exception("Failed to register Telegram bot commands.")
        logger.info("Telegram Bot polling started.")
        while self.running:
            try:
                self.poll_once()
            except Exception as e:
                logger.error(f"Error in poll loop: {e}")
                time.sleep(5)
            time.sleep(1)
