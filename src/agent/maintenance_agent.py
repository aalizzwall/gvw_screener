import os
import glob
import json
import re
import sqlite3
import logging
from typing import Dict, Any, List, Optional

from src.config import DATA_DIR, LOGS_DIR
from src.state.interfaces import IStateRepository
from src.state.state_manager import StateManager
from src.state.ai_provider import AIProviderPreferenceStore
from src.diagnostics.doctor import GVWDoctor
from src.agent.tool_registry import ToolRegistry
from src.agent.session_context import SessionContext
from src.agent.shell_tools import ShellToolkit
from src.analysis.model_prober import get_intra_provider_pool
from src.analysis.ai_analyst import (
    _PROVIDERS,
    _provider_key,
    _resolve_model,
    _request_completion,
)

logger = logging.getLogger("gvw.agent.maintenance")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAX_TURNS = 12


class MaintenanceAgent:
    """
    Interactive Copilot / Maintenance Agent inspired by Pi Agent's architecture.
    Features:
    - Minimalist ReAct Tool-Calling loop with error self-correction.
    - Modular ToolRegistry with dynamic schema generation.
    - Multi-turn SessionContext with sliding window context compaction.
    - Comprehensive safe read-only system diagnostic tools.
    """

    def __init__(
        self,
        state_manager: Optional[IStateRepository] = None,
        ai_provider_store: Optional[AIProviderPreferenceStore] = None,
        doctor: Optional[GVWDoctor] = None,
        scheduler: Optional[Any] = None,
        logs_dir: str = LOGS_DIR,
        data_dir: str = DATA_DIR,
    ):
        self.state_manager = state_manager or StateManager()
        self.ai_provider_store = ai_provider_store or AIProviderPreferenceStore()
        self.doctor = doctor or GVWDoctor(
            data_dir=data_dir,
            logs_dir=logs_dir,
            ai_provider_store=self.ai_provider_store,
        )
        self.scheduler = scheduler
        self.logs_dir = logs_dir
        self.data_dir = data_dir

        self.sessions: Dict[str, SessionContext] = {}
        self.shell = ShellToolkit(cwd=PROJECT_ROOT)
        self.registry = ToolRegistry()
        self._register_default_tools()

    def _register_default_tools(self):
        """Registers built-in safe read-only diagnostic tools into registry."""
        self.registry.register_tool(
            name="inspect_stock",
            description="查看特定個股在資料庫中的完整快照 (basic_info、financials、prices 等實體欄位)。",
            handler=self.tool_inspect_stock,
            parameters={
                "ticker": {"type": "string", "description": "台股代號，如 '2330'", "required": True}
            },
        )
        self.registry.register_tool(
            name="search_logs",
            description="搜尋 logs/gvw_bot.log 最近符合關鍵字的日誌。",
            handler=self.tool_search_logs,
            parameters={
                "keyword": {"type": "string", "description": "搜尋關鍵字，可為空", "required": False, "default": ""},
                "max_lines": {"type": "integer", "description": "回傳最多行數", "required": False, "default": 30},
            },
        )
        self.registry.register_tool(
            name="inspect_db_schema",
            description="查看 SQLite 資料庫表結構與記錄筆數。",
            handler=self.tool_inspect_db_schema,
            parameters={},
        )
        self.registry.register_tool(
            name="get_provider_endpoints",
            description="查看目前各資料源 (TWSE, FinMind, Yahoo) 呼叫的 API 端點及所提供的欄位。",
            handler=self.tool_get_provider_endpoints,
            parameters={},
        )
        self.registry.register_tool(
            name="inspect_system_health",
            description="查看硬體、排程、心跳與連線健康報告。",
            handler=self.tool_inspect_system_health,
            parameters={},
        )
        self.registry.register_tool(
            name="inspect_scheduler_jobs",
            description="查看系統定時排程任務列表、執行間隔、下次觸發時間與目前執行狀態。",
            handler=self.tool_inspect_scheduler_jobs,
            parameters={},
        )
        self.registry.register_tool(
            name="inspect_ai_config",
            description="查看當前 AI 模型優先鏈、主力模型、本地 LM-Studio/Ollama 端點及健康配置。",
            handler=self.tool_inspect_ai_config,
            parameters={},
        )

        # Pi-style core primitives: full access to the host to investigate and fix issues.
        self.registry.register_tool(
            name="bash",
            description=(
                "在這台主機上執行任意 bash 指令 (systemctl、journalctl、df、free、ps、curl、sqlite3、git、pip 等)，"
                "回傳 stdout/stderr/exit_code。無互動式輸入，預設逾時 60 秒；高風險破壞性指令會被拒絕。"
            ),
            handler=self.shell.bash,
            parameters={
                "command": {"type": "string", "description": "要執行的 bash 指令", "required": True},
                "timeout": {"type": "integer", "description": "逾時秒數 (最大 180)", "required": False, "default": 60},
            },
        )
        self.registry.register_tool(
            name="read_file",
            description="讀取任意檔案 (日誌、設定、原始碼)，回傳含行號內容，可用 offset/limit 分段。機敏欄位會自動遮蔽。",
            handler=self.shell.read_file,
            parameters={
                "path": {"type": "string", "description": "檔案路徑 (絕對或相對專案根目錄)", "required": True},
                "offset": {"type": "integer", "description": "起始行號 (從 1 起)", "required": False, "default": 1},
                "limit": {"type": "integer", "description": "最多讀取行數", "required": False, "default": 400},
            },
        )
        self.registry.register_tool(
            name="write_file",
            description="建立或覆寫檔案 (例如修復腳本、設定檔)。不可寫入 .env、SSH 金鑰與 .git 內部檔案。",
            handler=self.shell.write_file,
            parameters={
                "path": {"type": "string", "description": "檔案路徑", "required": True},
                "content": {"type": "string", "description": "完整檔案內容", "required": True},
            },
        )
        self.registry.register_tool(
            name="edit_file",
            description="精準替換檔案中唯一出現的一段文字。修改前務必先 read_file 確認原文。",
            handler=self.shell.edit_file,
            parameters={
                "path": {"type": "string", "description": "檔案路徑", "required": True},
                "old_text": {"type": "string", "description": "要被取代的原文 (必須唯一)", "required": True},
                "new_text": {"type": "string", "description": "取代後的新文字", "required": True},
            },
        )

    # ---------------------------------------------------------
    # Safe Read-Only Diagnostic Tools
    # ---------------------------------------------------------
    def tool_inspect_stock(self, ticker: str) -> Dict[str, Any]:
        """Inspects saved database state for a specific stock ticker."""
        clean_ticker = str(ticker).strip()
        stock = self.state_manager.get_stock_data(clean_ticker)
        if not stock:
            return {"found": False, "message": f"資料庫中無 {clean_ticker} 的資料記錄。"}

        basic_info = stock.get("basic_info", {})
        if isinstance(basic_info, list):
            basic_info = basic_info[-1] if basic_info else {}
        if not isinstance(basic_info, dict):
            basic_info = {}

        financials = stock.get("financials", {})
        prices = stock.get("prices", [])
        return {
            "found": True,
            "ticker": clean_ticker,
            "source": stock.get("source", "unknown"),
            "price_data_as_of": stock.get("price_data_as_of"),
            "prices_count": len(prices) if isinstance(prices, list) else 0,
            "basic_info_keys": list(basic_info.keys()),
            "basic_info": basic_info,
            "has_financials": bool(financials),
            "financials_keys": list(financials.keys()) if isinstance(financials, dict) else [],
            "income_statement": financials.get("income_statement", {}) if isinstance(financials, dict) else {},
            "balance_sheet": financials.get("balance_sheet", {}) if isinstance(financials, dict) else {},
        }

    def tool_search_logs(self, keyword: str = "", max_lines: int = 30) -> str:
        """Searches recent log lines matching a specific keyword."""
        log_file = os.path.join(self.logs_dir, "gvw_bot.log")
        if not os.path.exists(log_file):
            return "查無日誌檔案 (gvw_bot.log)。"

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()

            recent = all_lines[-400:]
            if keyword:
                matched = [l.strip() for l in recent if keyword.lower() in l.lower()]
            else:
                matched = [l.strip() for l in recent]

            selected = matched[-max_lines:]
            if not selected:
                return f"最近 400 行日誌中無包含 '{keyword}' 的記錄。"
            return "\n".join(selected)
        except Exception as e:
            return f"讀取日誌失敗：{e}"

    def tool_inspect_db_schema(self) -> Dict[str, Any]:
        """Inspects SQLite database schema and row counts."""
        db_files = glob.glob(os.path.join(self.data_dir, "*.db"))
        overview = {}
        for db in db_files:
            name = os.path.basename(db)
            try:
                conn = sqlite3.connect(db, timeout=5)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]
                table_info = {}
                for t in tables:
                    cursor.execute(f"SELECT COUNT(*) FROM {t};")
                    count = cursor.fetchone()[0]
                    cursor.execute(f"PRAGMA table_info({t});")
                    cols = [col[1] for col in cursor.fetchall()]
                    table_info[t] = {"row_count": count, "columns": cols}
                conn.close()
                overview[name] = table_info
            except Exception as e:
                overview[name] = {"error": str(e)}
        return overview

    def tool_get_provider_endpoints(self) -> Dict[str, Any]:
        """Returns API endpoints and field mappings for active data providers."""
        return {
            "twse": {
                "name": "Taiwan Stock Exchange OpenAPI (TWSE)",
                "base_url": "https://openapi.twse.com.tw/v1",
                "endpoints": {
                    "basic_info": "/opendata/t187ap03_L (公司基本資料: 資本額、產業別)",
                    "valuation": "/exchangeReport/BWIBBU_d (提供個股日 PE、PB、殖利率)",
                    "financials_pl": "/opendata/t187ap06_L_ci (綜合損益表)",
                    "financials_bs": "/opendata/t187ap07_L_ci (資產負債表)",
                    "daily_all": "/exchangeReport/STOCK_DAY_ALL (每日收盤資訊)",
                },
            },
            "finmind": {
                "name": "FinMind API",
                "datasets": {
                    "TaiwanStockPER": "提供 PER(本益比)、PBR(股價淨值比)、dividend_yield(股利殖利率)",
                    "TaiwanStockPrice": "歷史 K 線",
                    "TaiwanStockFinancialStatements": "財務報表",
                },
            },
            "yahoo_finance": {
                "name": "Yahoo Finance (yfinance)",
                "fields": "trailing_pe, price_to_book, dividend_yield, history",
            },
        }

    def tool_inspect_system_health(self) -> Dict[str, Any]:
        """Runs doctor diagnostics covering hardware, scheduler, and database."""
        return self.doctor.run_diagnostics()

    def tool_inspect_scheduler_jobs(self) -> Dict[str, Any]:
        """Inspects status of registered scheduler jobs."""
        if not self.scheduler or not hasattr(self.scheduler, "jobs"):
            return {"available": False, "message": "目前環境未掛載排程器 (Scheduler)。"}

        jobs_data = []
        for j in self.scheduler.jobs:
            timing = (
                f"every {j.interval_minutes}m"
                if j.interval_minutes
                else f"daily {j.daily_time}"
            )
            jobs_data.append({
                "name": j.name,
                "schedule": timing,
                "weekdays_only": getattr(j, "weekdays_only", False),
                "is_running": getattr(j, "is_running", False),
                "last_run": str(j.last_run) if getattr(j, "last_run", None) else "never",
            })
        return {
            "available": True,
            "job_count": len(jobs_data),
            "jobs": jobs_data,
        }

    def tool_inspect_ai_config(self) -> Dict[str, Any]:
        """Inspects current AI provider chain and local connection."""
        chain = self.ai_provider_store.get_chain()
        local_url = self.ai_provider_store.get_local_base_url()
        return {
            "primary_provider": chain[0] if chain else "none",
            "provider_chain": chain,
            "local_base_url": local_url,
            "configured_providers": list(_PROVIDERS.keys()),
        }

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Executes a diagnostic tool via registry."""
        safe_args = {
            k: ("[REDACTED]" if any(s in str(k).lower() for s in ("key", "token", "pass", "secret")) else v)
            for k, v in args.items()
        }
        logger.info("MaintenanceAgent executing tool: %s with args %s", tool_name, safe_args)
        return self.registry.execute(tool_name, args)

    # ---------------------------------------------------------
    # Multi-turn Session Management
    # ---------------------------------------------------------
    def get_or_create_session(self, session_id: str) -> SessionContext:
        """Retrieves or creates a session context for continuous chat."""
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionContext(session_id=session_id)
        return self.sessions[session_id]

    def clear_session(self, session_id: str):
        """Clears memory for a specific session."""
        if session_id in self.sessions:
            self.sessions[session_id].clear()
            logger.info(f"Cleared session context for {session_id}")

    # ---------------------------------------------------------
    # ReAct / Tool-Calling Reasoning Loop
    # ---------------------------------------------------------
    def _call_llm(self, messages: List[Dict[str, str]]) -> str:
        """Invokes active LLM provider chain with Two-Tier model and provider fallback."""
        chain = self.ai_provider_store.get_chain()
        local_url = self.ai_provider_store.get_local_base_url()

        for entry in chain:
            name, model = entry.split("/", 1) if "/" in entry else (entry, "")
            if name not in _PROVIDERS:
                continue
            key = _provider_key(name, local_base_url=local_url)
            if not key:
                continue

            # Tier 1: Intra-provider model pool fallback
            pool = get_intra_provider_pool(name, key, local_base_url=local_url, preferred_model=model)
            for cand_model in pool:
                try:
                    actual_model = _resolve_model(name, cand_model, local_base_url=local_url)
                    return _request_completion(name, key, actual_model, messages, max_tokens=2500)
                except Exception as e:
                    logger.warning(
                        f"MaintenanceAgent LLM call failed with {name}/{cand_model}: {e}"
                    )
                    status = getattr(getattr(e, "response", None), "status_code", None)
                    if status == 401:
                        # Auth failure affects whole provider; cascade to next provider (Tier 2)
                        break
                    continue

        raise RuntimeError("所有 AI Provider 與備援模型均無法調用，無法進行維護模式診斷。")

    def _parse_json_response(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """
        Robust JSON extractor for LLM outputs.
        Handles markdown code fences, trailing commas, and text wrappers.
        """
        cleaned = raw_text.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

        # Try direct parse
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and "action" in parsed:
                return parsed
        except Exception:
            # Fallback to regex pattern extraction if raw JSON parse fails
            pass

        # Try regex search for {"action": ...}
        match = re.search(r"\{[\s\S]*\"action\"[\s\S]*\}", cleaned)
        if match:
            candidate = match.group(0)
            # Remove trailing commas before closing braces/brackets
            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and "action" in parsed:
                    return parsed
            except Exception:
                # Candidate snippet is not valid JSON; continue to return None
                pass

        return None

    def ask(self, user_question: str, session_id: Optional[str] = None) -> str:
        """
        Processes user question through dynamic tool-calling and continuous session memory.
        Returns detailed, diagnostic answer formatted in Telegram HTML.
        """
        # Session context
        if session_id:
            session = self.get_or_create_session(session_id)
        else:
            session = SessionContext(session_id="ephemeral")

        session.add_user_message(f"負責人問題：{user_question}")

        tools_doc = self.registry.generate_prompt_documentation()
        system_prompt = (
            "你是 GVW 智能選股系統所在這台主機 (Raspberry Pi) 的系統管理員 (Maintenance Copilot)。\n"
            f"專案目錄：{PROJECT_ROOT}；服務為 systemd user service `gvw_bot`。\n"
            "你的任務是自主找出並解決資料缺失、排程異常、服務故障、網路或環境問題。\n"
            "你擁有 bash / read_file / write_file / edit_file，可自由探索整台主機。\n"
            "原則：\n"
            "1. 先用唯讀指令查證 (journalctl、systemctl status、df、free、ps、curl、sqlite3、git log)，不可憑空猜測。\n"
            "2. 修改檔案或重啟服務前，先說明原因；修改前必須 read_file 確認原文。\n"
            "3. 不做破壞性操作 (刪除資料、格式化、關機)，不讀取或洩漏金鑰；修改程式碼請盡量小幅並說明如何還原。\n"
            "4. 最終回報須包含：根因、你做了什麼、目前狀態、建議後續。\n\n"
            + tools_doc
            + "\n\n"
            "【Action 協議規範】\n"
            "當你需要調用工具查詢時，請只回傳以下 JSON 格式 (不要有其他多餘文字)：\n"
            "{\n"
            '  "action": "<tool_name>",\n'
            '  "args": {"<arg_name>": "<arg_value>"}\n'
            "}\n\n"
            "當你調用工具並獲得足夠的數據依據後，請回傳最終診斷結論，格式為：\n"
            "{\n"
            '  "action": "final_answer",\n'
            '  "answer": "<你的繁體中文診斷分析與具體修復建議>"\n'
            "}"
        )

        max_turns = MAX_TURNS
        for turn in range(max_turns):
            logger.info(f"MaintenanceAgent turn {turn + 1}/{max_turns}...")
            messages = session.get_messages(system_prompt)
            raw_response = self._call_llm(messages)

            action_dict = self._parse_json_response(raw_response)

            if not action_dict:
                # Model directly returned plain text diagnosis
                answer = raw_response.strip()
                session.add_assistant_message(answer)
                return answer

            action = action_dict.get("action")
            if action == "final_answer":
                answer = action_dict.get("answer", raw_response).strip()
                session.add_assistant_message(answer)
                return answer

            # Execute tool safely
            args = action_dict.get("args", {})
            if not isinstance(args, dict):
                args = {}
            tool_res = self._execute_tool(action, args)

            # Record turn in session memory
            session.add_assistant_message(json.dumps(action_dict, ensure_ascii=False))
            session.add_observation(action, tool_res)

        warning_msg = f"⚠️ 已達到最大工具調用深度（{MAX_TURNS} 回合），未能完全收斂診斷，請嘗試進一步縮小問題範圍或重置對話。"
        session.add_assistant_message(warning_msg)
        return warning_msg
