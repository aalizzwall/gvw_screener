import os
import glob
import json
import re
import shutil
import sqlite3
import subprocess
import logging
from typing import Dict, Any, List, Optional, Set

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
MAX_TURNS = 5


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
        self.registry.register_tool(
            name="inspect_github_health",
            description="執行 GitHub 倉庫健康巡檢，檢查 CodeQL 弱點掃描警報、CI/CD 失敗紀錄與待追蹤 Issues。",
            handler=self.tool_inspect_github_health,
            parameters={},
        )
        self.registry.register_tool(
            name="pi_agent",
            description="呼叫主機上的 Pi Coding Agent (pi CLI) 執行高階專案程式碼審視、複雜問題排查或自主修復建議。",
            handler=self.tool_pi_agent,
            parameters={
                "prompt": {"type": "string", "description": "要發送給 Pi Agent 的任務提示詞", "required": True},
                "timeout": {"type": "integer", "description": "逾時秒數 (預設 120 秒)", "required": False, "default": 120},
            },
        )
        self.registry.register_tool(
            name="inspect_system_load",
            description="檢查主機負載 (Load Average) 與是否有程序卡在磁碟 I/O (D 狀態) 或過度佔用 CPU (R 狀態)。",
            handler=self.tool_inspect_system_load,
            parameters={},
        )
        self.registry.register_tool(
            name="inspect_log2ram_and_disk",
            description="檢查主機磁碟空間與樹莓派 /var/log (log2ram 記憶體虛擬磁碟) 佔用率，提早防範磁碟滿載導致系統崩潰。",
            handler=self.tool_inspect_log2ram_and_disk,
            parameters={},
        )
        self.registry.register_tool(
            name="clean_system_logs",
            description="清理系統過期日誌並對 journal 進行真空壓縮 (journalctl vacuum)，釋放 /var/log 記憶體磁碟空間。",
            handler=self.tool_clean_system_logs,
            parameters={
                "max_size_mb": {"type": "integer", "description": "保留的最大 journal 大小 (MB，預設 20)", "required": False, "default": 20},
            },
        )
        self.registry.register_tool(
            name="check_service_health",
            description="檢查 systemd (系統層與使用者層) 是否有失敗 (failed) 或處於無窮自動重啟 (flapping) 的服務。",
            handler=self.tool_check_service_health,
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

    def tool_inspect_github_health(self) -> Dict[str, Any]:
        """Runs automated audit of GitHub repository code scanning and workflow runs."""
        from src.diagnostics.github_auditor import GitHubAuditor

        return GitHubAuditor().run_full_audit()

    def _get_pi_model_candidates(self) -> List[str]:
        """Maps active provider chain into pi CLI model strings, guaranteeing local/active at the end."""
        chain = self.ai_provider_store.get_chain()
        mapped: List[str] = []
        for entry in chain:
            if "/" not in entry:
                continue
            prov, model = entry.split("/", 1)
            clean_model = model.replace("models/", "").strip()
            if prov in ("gemma", "google"):
                mapped.append(f"google/{clean_model}")
            elif prov == "openai":
                mapped.append(f"openai/{clean_model}")
            elif prov == "local":
                mapped.append(f"local/{clean_model}")

        # Ensure default cloud models are present if chain is empty or missing them
        for default_cand in ["google/gemini-3.5-flash", "google/gemini-3.5-flash-lite", "openai/gpt-4o-mini"]:
            if default_cand not in mapped:
                mapped.insert(len(mapped) - 1 if "local/active" in mapped else len(mapped), default_cand)

        # De-duplicate while preserving strength order
        deduped: List[str] = []
        for m in mapped:
            if m not in deduped:
                deduped.append(m)

        # Guarantee local/active is strictly at the tail end
        if "local/active" in deduped:
            deduped.remove("local/active")
        deduped.append("local/active")

        return deduped

    def tool_pi_agent(self, prompt: str, timeout: int = 120) -> Dict[str, Any]:
        """
        Invokes Pi Coding Agent CLI on host with automated strength-ranked fallback.
        Tries cloud models first by capability, cascading to local/active as the ultimate safety net.
        """
        pi_bin = shutil.which("pi") or "/usr/bin/pi"
        if not (pi_bin and os.path.exists(pi_bin)):
            return {
                "success": False,
                "error": "主機上未安裝 pi CLI，請先確認已安裝 @earendil-works/pi-coding-agent。",
            }

        safe_timeout = min(max(int(timeout), 10), 180)
        candidates = self._get_pi_model_candidates()
        from src.agent.shell_tools import redact_secrets, _truncate

        last_error = ""
        attempted_models: List[str] = []

        for model_id in candidates:
            attempted_models.append(model_id)
            logger.info(f"Attempting Pi Agent execution with model: {model_id}...")
            try:
                res = subprocess.run(
                    [pi_bin, "-p", "--model", model_id, str(prompt)],
                    cwd=PROJECT_ROOT,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=safe_timeout,
                )
                out = res.stdout.strip()
                err = res.stderr.strip()

                if res.returncode == 0 and out:
                    return {
                        "success": True,
                        "model_used": model_id,
                        "exit_code": 0,
                        "output": _truncate(redact_secrets(out)),
                        "attempted_models": attempted_models,
                    }

                error_detail = err or out
                last_error = f"Model {model_id} failed (code {res.returncode}): {error_detail[:200]}"
                logger.warning(f"Pi Agent model {model_id} failed: {error_detail[:150]}, falling back to next candidate...")

            except subprocess.TimeoutExpired:
                last_error = f"Model {model_id} timed out after {safe_timeout}s"
                logger.warning(last_error)
            except Exception as e:
                last_error = f"Model {model_id} unexpected exception: {e}"
                logger.warning(last_error)

        return {
            "success": False,
            "error": f"所有候選模型均調用失敗：{last_error}",
            "attempted_models": attempted_models,
        }

    def tool_inspect_system_load(self) -> Dict[str, Any]:
        """Inspects host CPU load average and processes in R (running) or D (disk I/O wait) state."""
        load_avg = [0.0, 0.0, 0.0]
        if os.path.exists("/proc/loadavg"):
            try:
                with open("/proc/loadavg", "r") as f:
                    parts = f.read().strip().split()
                    load_avg = [float(parts[0]), float(parts[1]), float(parts[2])]
            except Exception:
                pass

        cmd = "ps -eo pid,stat,%cpu,%mem,wchan:20,comm,args | grep -E '^[[:space:]]*[0-9]+[[:space:]]+[RD]'"
        res = self.shell.bash(cmd, timeout=10)
        lines = res.get("stdout", "").strip().splitlines() if res.get("stdout") else []

        d_processes = [l for l in lines if re.search(r'\b[D][a-zA-Z]*\b', l)]
        r_processes = [l for l in lines if re.search(r'\b[R][a-zA-Z]*\b', l)]

        return {
            "load_average_1m_5m_15m": load_avg,
            "blocked_io_processes_count": len(d_processes),
            "blocked_io_processes": d_processes[:10],
            "cpu_active_processes_count": len(r_processes),
            "cpu_active_processes": r_processes[:10],
            "warning": "⚠️ 發現有程序卡在磁碟 I/O (D 狀態)，可能為隨身碟/磁碟掉盤或卡死！" if d_processes else "系統進程狀態正常，無磁碟 I/O 阻塞。",
        }

    def tool_inspect_log2ram_and_disk(self) -> Dict[str, Any]:
        """Inspects /var/log and root partition disk space, warning if log2ram is full."""
        df_res = self.shell.bash("df -h / /var/log 2>/dev/null", timeout=10)
        df_out = df_res.get("stdout", "")

        is_log2ram = "log2ram" in df_out
        log2ram_full = False
        log_usage_pct = 0

        for line in df_out.splitlines():
            if "/var/log" in line or "log2ram" in line:
                m = re.search(r'(\d+)%', line)
                if m:
                    log_usage_pct = int(m.group(1))
                    if log_usage_pct >= 80:
                        log2ram_full = True

        return {
            "df_output": df_out,
            "has_log2ram": is_log2ram,
            "log2ram_usage_pct": log_usage_pct,
            "is_critical": log2ram_full,
            "warning": "🚨 /var/log 記憶體磁碟使用率超過 80%，可能即將引發 No space left on device 故障！建議呼叫 clean_system_logs 釋放。" if log2ram_full else "磁碟與日誌空間充足。",
        }

    def tool_clean_system_logs(self, max_size_mb: int = 20) -> Dict[str, Any]:
        """Vacuums systemd journals and rotates logs to free up /var/log space."""
        clean_cmd = f"sudo journalctl --vacuum-size={max_size_mb}M 2>&1; sudo rm -f /var/log/*.1 /var/log/*.old 2>/dev/null; sudo logrotate /etc/logrotate.conf 2>&1; df -h /var/log"
        res = self.shell.bash(clean_cmd, timeout=30)
        return {
            "success": res.get("exit_code") == 0,
            "output": res.get("stdout") or res.get("stderr"),
        }

    def tool_check_service_health(self) -> Dict[str, Any]:
        """Checks for failed or flapping systemd units."""
        cmd = "systemctl list-units --state=failed --no-legend 2>&1; echo '---'; systemctl --user list-units --state=failed --no-legend 2>&1"
        res = self.shell.bash(cmd, timeout=10)
        out = res.get("stdout", "")
        parts = out.split("---")
        sys_failed = [l.strip() for l in parts[0].splitlines() if l.strip()]
        user_failed = [l.strip() for l in parts[1].splitlines() if l.strip()] if len(parts) > 1 else []

        return {
            "system_failed_units": sys_failed,
            "user_failed_units": user_failed,
            "has_failed_units": bool(sys_failed or user_failed),
            "message": "系統與使用者服務皆運行正常。" if not (sys_failed or user_failed) else f"發現異常服務：{sys_failed + user_failed}",
        }

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Executes a diagnostic tool via registry."""
        logger.info(f"MaintenanceAgent executing tool: {tool_name} with args: {args}")
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
    def _call_llm(
        self,
        messages: List[Dict[str, str]],
        failed_models: Optional[Set[str]] = None,
    ) -> str:
        """Invokes active LLM provider chain with Two-Tier model and provider fallback."""
        chain = self.ai_provider_store.get_chain()
        local_url = self.ai_provider_store.get_local_base_url()
        skip_set = failed_models if failed_models is not None else set()

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
                model_key = f"{name}/{cand_model}"
                if model_key in skip_set:
                    continue

                try:
                    actual_model = _resolve_model(name, cand_model, local_base_url=local_url)
                    return _request_completion(name, key, actual_model, messages, max_tokens=2500)
                except Exception as e:
                    logger.warning(
                        f"MaintenanceAgent LLM call failed with {name}/{cand_model}: {e}"
                    )
                    skip_set.add(model_key)
                    status = getattr(getattr(e, "response", None), "status_code", None)
                    err_msg = str(e).lower()
                    if (
                        status == 401
                        or "credit_balance_exhausted" in err_msg
                        or "insufficient_quota" in err_msg
                    ):
                        # Auth failure or zero credits affects whole provider; cascade to next provider (Tier 2)
                        for m in pool:
                            skip_set.add(f"{name}/{m}")
                        break
                    continue

        if skip_set and failed_models is not None:
            failed_models.clear()

        raise RuntimeError("所有 AI Provider 與備援模型均無法調用，無法進行維護模式診斷。")

    def _normalize_action_dict(self, action_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-repairs common minor LLM argument discrepancies (e.g. bash args)."""
        action = str(action_dict.get("action", "")).strip()
        args = action_dict.get("args")

        if not isinstance(args, dict):
            if action == "bash" and isinstance(args, str):
                args = {"command": args.strip()}
            elif action == "inspect_stock" and isinstance(args, (str, int)):
                args = {"ticker": str(args).strip()}
            else:
                args = {}

        if action == "bash" and "command" not in args and args:
            first_k = next(iter(args.keys()))
            args["command"] = first_k if not args[first_k] else str(args[first_k])

        action_dict["args"] = args
        return action_dict

    def _parse_json_response(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """
        Robust JSON extractor for LLM outputs.
        Handles markdown code fences, trailing commas, inner code blocks, malformed args sets, and text wrappers.
        """
        cleaned = raw_text.strip()

        # 1. Try direct parse first before touching code fences (avoids breaking internal code blocks)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and "action" in parsed:
                return self._normalize_action_dict(parsed)
        except Exception:
            pass

        # 2. Extract outer markdown code fence (```json ... ``` or ``` ... ```) if wrapped
        fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", cleaned)
        if fence_match:
            candidate = fence_match.group(1).strip()
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and "action" in parsed:
                    return self._normalize_action_dict(parsed)
            except Exception:
                pass

        # 3. Search outermost matching { ... } containing "action"
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start : end + 1]
            candidate_clean = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                parsed = json.loads(candidate_clean)
                if isinstance(parsed, dict) and "action" in parsed:
                    return self._normalize_action_dict(parsed)
            except Exception:
                pass

        # 4. Fallback regex specifically extracting final_answer or answer if JSON was malformed
        match_final = re.search(
            r'"action"\s*:\s*"final_answer"[\s\S]*?"answer"\s*:\s*"([\s\S]*?)"\s*\}',
            cleaned,
        )
        if match_final:
            ans_raw = match_final.group(1)
            try:
                ans_clean = json.loads(f'"{ans_raw}"')
            except Exception:
                ans_clean = ans_raw.replace('\\n', '\n').replace('\\"', '"')
            return {"action": "final_answer", "answer": ans_clean}

        # 5. Regex extraction for malformed actions like {"action": "bash", "args": {"uptime"}}
        action_match = re.search(r'"action"\s*:\s*"([a-zA-Z0-9_-]+)"', cleaned)
        if action_match:
            action_name = action_match.group(1).strip()
            if action_name == "final_answer":
                return {"action": "final_answer", "answer": cleaned}

            args_dict: Dict[str, Any] = {}
            if action_name == "bash":
                cmd_match = re.search(r'"(?:command|args)"\s*:\s*(?:\{\s*)?"([^"]+)"', cleaned)
                if cmd_match:
                    args_dict = {"command": cmd_match.group(1).strip()}
            elif action_name == "inspect_stock":
                ticker_match = re.search(r'"(?:ticker|args)"\s*:\s*(?:\{\s*)?"?(\d{4,6})"?', cleaned)
                if ticker_match:
                    args_dict = {"ticker": ticker_match.group(1).strip()}

            return {"action": action_name, "args": args_dict}

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
            "你擁有 bash / read_file / write_file / edit_file，可探索排查主機。\n"
            "【關鍵原則】：\n"
            "1. 請保持精簡高效！通常在調用 1~2 次工具（例如檢查 uptime、ps、free、df、systemctl status）取得核心資訊後，即應回傳 final_answer，嚴禁無意義的連續多輪深度探索。\n"
            "2. 先用唯讀指令查證，不可憑空猜測；若只是架構或狀態查詢，直接統整後回傳結論。\n"
            "3. 不做破壞性操作 (刪除資料、格式化、關機)，不讀取或洩漏金鑰。\n"
            "4. 最終回報須包含繁體中文：根因/結論、執行操作、目前狀態、建議後續。\n\n"
            + tools_doc
            + "\n\n"
            "【Action 協議規範】\n"
            "當你需要調用工具查詢時，請只回傳以下 JSON 格式 (不要有其他多餘文字，注意 args 必須是字典鍵值對)：\n"
            "{\n"
            '  "action": "<tool_name>",\n'
            '  "args": {"<arg_name>": "<arg_value>"}\n'
            "}\n"
            "呼叫工具範例：\n"
            '{"action": "bash", "args": {"command": "uptime"}}\n'
            '{"action": "inspect_system_load", "args": {}}\n'
            '{"action": "inspect_stock", "args": {"ticker": "2330"}}\n\n'
            "當你調用工具並獲得足夠的數據依據後，請立即回傳最終診斷結論，格式為：\n"
            "{\n"
            '  "action": "final_answer",\n'
            '  "answer": "<你的繁體中文診斷分析與具體修復建議>"\n'
            "}"
        )

        max_turns = MAX_TURNS
        failed_models_in_session: Set[str] = set()
        for turn in range(max_turns):
            logger.info(f"MaintenanceAgent turn {turn + 1}/{max_turns}...")
            messages = session.get_messages(system_prompt)
            raw_response = self._call_llm(messages, failed_models=failed_models_in_session)

            action_dict = self._parse_json_response(raw_response)

            if not action_dict:
                # If model clearly attempted an action (mentions "action":), prevent protocol leak
                if '"action"' in raw_response:
                    logger.warning("Model emitted unparseable action JSON, requesting self-correction...")
                    session.add_assistant_message(raw_response)
                    session.add_observation(
                        "system",
                        "格式錯誤：請務必回傳標準 JSON 格式呼叫工具，例如：\n"
                        '{"action": "bash", "args": {"command": "uptime"}}\n'
                        '{"action": "final_answer", "answer": "<繁體中文分析結論>"}'
                    )
                    continue

                # Model directly returned natural language plain text
                session.add_assistant_message(raw_response.strip())
                return raw_response.strip()

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
