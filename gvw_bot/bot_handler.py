import os
import sys
import time
import logging
import html
import subprocess
import threading
from typing import Optional, Dict, Any, List, Union
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
from src.analysis.ai_analyst import analyze_stock, check_providers
from src.state.ai_access import AIAnalysisAccessStore
from src.state.ai_provider import AIProviderPreferenceStore, model_catalog
from src.reporting.report_engine import generate_daily_report
from src.analysis.ranker import run_ranking
from src.core.orchestrator import Orchestrator
from src.analysis.intraday import run_intraday_monitoring
from src.diagnostics.doctor import GVWDoctor
from src.agent.issue_solver import IssueSolver, SecurityGuardrailError
from src.agent.maintenance_agent import MaintenanceAgent
from src.state.alert_subscribers import AlertSubscriptionStore

logger = logging.getLogger("gvw.bot")

BOT_COMMANDS = [
    {"command": "report", "description": "查看 GVW 選股報告與 Top 10"},
    {"command": "toggle_monitor", "description": "開啟或關閉盤中突破即時推播"},
    {"command": "status", "description": "查看服務狀態"},
    {"command": "monitor", "description": "執行盤中信號掃描"},
    {"command": "collect", "description": "手動採集股票資料"},
    {"command": "rank", "description": "重新計算股票排名"},
    {"command": "jobs", "description": "查看排程任務"},
    {"command": "analyze", "description": "使用 AI 評估個股，例如 /analyze 2330"},
    {"command": "request_ai", "description": "申請使用 AI 個股分析"},
    {"command": "ai_requests", "description": "審核 AI 分析使用申請"},
    {"command": "ai_status", "description": "查看 AI provider/model 順序"},
    {"command": "ai_models", "description": "列出可選的 AI provider/model"},
    {"command": "ai_set", "description": "設定順序，例如 /ai_set openai/gpt-4o gemma/gemma-3-27b-it"},
    {"command": "ai_add", "description": "加入 provider/model，可指定位置"},
    {"command": "ai_remove", "description": "移除 provider/model 或序號"},
    {"command": "ai_check", "description": "檢測可用性並將可用者排前面"},
    {"command": "ai_local", "description": "查看或設定地端 LM-Studio 端點 URL"},
    {"command": "update", "description": "一鍵從 GitHub 拉取最新代碼並重啟服務"},
    {"command": "doctor", "description": "系統全方位健康診斷，/doctor fix 可執行自愈"},
    {"command": "fix", "description": "自主修復指定 Issue，例如 /fix #28"},
    {"command": "maintain", "description": "切換系統維護模式，可直接用自然語言診斷資料與除錯"},
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
    "🩺 系統狀態": "/status",
    "🔍 盤中監控": "/monitor",
    "📥 採集資料": "/collect",
    "🏆 重新排名": "/rank",
    "⏰ 排程任務": "/jobs",
}
REPLY_KEYBOARD = {
    "keyboard": [
        ["🏆 Top 10 選股", "🔕 盤中監控"],
        ["🤖 AI 個股分析"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
PUBLIC_REPLY_KEYBOARD = {
    "keyboard": [
        ["🏆 Top 10 選股", "🔕 盤中監控"],
        ["📝 申請 AI 分析"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


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

    def send_message(
        self,
        chat_id: Union[int, str],
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        user_id: Optional[Union[int, str]] = None,
    ) -> bool:
        if not self.token:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": (
                reply_markup if reply_markup is not None else self._keyboard_for_chat(chat_id, user_id=user_id)
            ),
        }
        try:
            r = requests.post(url, json=payload, timeout=20)
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Error sending telegram message: {e}")
            return False

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
                [ai_btn],
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
        owner_only_commands = {
            "/status", "/jobs", "/monitor",
            "/collect", "/rank", "/ai_requests",
            "/ai_status", "/ai_models", "/ai_set", "/ai_add", "/ai_remove", "/ai_check",
            "/ai_local", "/update", "/doctor", "/fix", "/maintain",
        }
        if cmd in owner_only_commands and not self._is_owner(user_id):
            self.send_message(chat_id, "⛔ 此功能僅限系統負責人使用。", user_id=user_id)
            return

        if cmd in ["/start", "/help"]:
            msg = (
                "🤖 <b>GVW Screener 智能選股機器人</b>\n\n"
                "本機器人已內建自動排程引擎，定時執行採集、選股與推播：\n"
                "• 08:30 晨間速報\n"
                "• 09:00-13:30 盤中突破即時監控 (每10分鐘)\n"
                "• 14:00 盤後全市場數據採集\n"
                "• 15:00 全局 GVW 評分排名\n"
                "• 15:30 盤後篩選總結日報\n\n"
                "<b>三大核心功能（可直接點擊下方鍵盤按鈕）：</b>\n"
                "1. 🏆 <b>Top 10 選股</b>：隨時查看最新全市場 GVW Top 10 核心優質標的\n"
                "2. 🔔 <b>盤中監控</b> / 🔕 <b>盤中監控</b>：開啟或關閉推播，當 Top 30 核心標的同時通過「分時三步法」突破信號時主動通知\n"
                "3. 🤖 <b>AI 個股分析</b> / 📝 <b>申請 AI 分析</b>：對個股進行基本面、動能與價量 AI 深度評估\n"
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

        elif cmd == "/ai_requests":
            requests_list = self.ai_access_store.pending_requests()
            if not requests_list:
                self.send_message(chat_id, "目前沒有待審核的 AI 使用申請。")
                return
            for request in requests_list:
                self._notify_owner_of_request(request)

        elif cmd == "/ai_status":
            self.send_message(chat_id, self._ai_status_text())

        elif cmd == "/ai_models":
            lines = ["📚 <b>可選的 provider/model</b>"]
            for provider, models in model_catalog().items():
                lines.append(f"\n<b>{provider}</b>")
                lines.extend(f"• <code>{provider}/{model}</code>" for model in models)
            self.send_message(chat_id, "\n".join(lines))

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
                    "維護 Agent 導入 Pi 極簡架構，支援多輪對話記憶與系統自我診斷。\n"
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

        else:
            self.send_message(chat_id, "未知指令，請輸入 /help 查看功能。")

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

            venv_pip = os.path.join(parent_dir, ".venv", "bin", "pip")
            req_file = os.path.join(parent_dir, "requirements.txt")
            if os.path.exists(venv_pip) and os.path.exists(req_file):
                logger.info("Updating dependencies via pip in .venv...")
                subprocess.run([venv_pip, "install", "-r", req_file], timeout=90, cwd=parent_dir)

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
            self.send_message(chat_id, f"🛠️ <b>【維護診斷報告】</b>\n\n{answer}")
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
        if not self._is_owner(actor_id):
            self._answer_callback_query(callback_id, "僅限系統負責人操作。")
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
        self.send_message(chat_id, f"⏳ 正在根據本機已採集資料分析 {ticker}，請稍候...")
        try:
            report = analyze_stock(ticker)
        except (RuntimeError, ValueError) as exc:
            self.send_message(chat_id, str(exc))
            return

        disclaimer = "AI 分析僅供參考，非投資建議。"
        if disclaimer not in report:
            report = f"{report}\n\n{disclaimer}"
        self.send_message(
            chat_id,
            f"🤖 <b>{ticker} 個股 AI 評估</b>\n\n{html.escape(report)}",
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

                    if text and chat_id and (
                        text.startswith("/")
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
                            chat_type=chat.get("type", "private"),
                            user=sender,
                        )
        except Exception as e:
            logger.debug(f"Telegram polling error: {e}")

    def register_commands(self):
        if not self.token:
            return

        # 1. Clear / delete commands for regular users (public default scope has NO menu)
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
