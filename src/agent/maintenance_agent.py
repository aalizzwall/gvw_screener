import os
import sys
import glob
import json
import time
import sqlite3
import logging
from typing import Dict, Any, List, Optional, Union

from src.config import DATA_DIR, LOGS_DIR
from src.state.state_manager import StateManager
from src.state.ai_provider import AIProviderPreferenceStore
from src.diagnostics.doctor import GVWDoctor
from src.analysis.ai_analyst import (
    _PROVIDERS,
    _provider_key,
    _resolve_model,
    _request_completion,
)

logger = logging.getLogger("gvw.agent.maintenance")


class MaintenanceAgent:
    """
    Interactive Copilot / Maintenance Agent that diagnoses data issues,
    system anomalies, and pipeline state through dynamic Tool Calling (ReAct).
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        ai_provider_store: Optional[AIProviderPreferenceStore] = None,
        doctor: Optional[GVWDoctor] = None,
        logs_dir: str = LOGS_DIR,
        data_dir: str = DATA_DIR,
    ):
        self.state_manager = state_manager or StateManager()
        self.ai_provider_store = ai_provider_store or AIProviderPreferenceStore()
        self.doctor = doctor or GVWDoctor(data_dir=data_dir, logs_dir=logs_dir, ai_provider_store=self.ai_provider_store)
        self.logs_dir = logs_dir
        self.data_dir = data_dir

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
                    "basic_info": "/opendata/t187ap03_L (僅公司登記基本資料: 資本額、產業別、無PE/PB/殖利率)",
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

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Executes a diagnostic tool by name."""
        logger.info(f"MaintenanceAgent invoking tool: {tool_name} with args {args}")
        if tool_name == "inspect_stock":
            ticker = args.get("ticker", "2330")
            return self.tool_inspect_stock(ticker)
        elif tool_name == "search_logs":
            kw = args.get("keyword", "")
            max_l = args.get("max_lines", 30)
            return self.tool_search_logs(keyword=kw, max_lines=max_l)
        elif tool_name == "inspect_db_schema":
            return self.tool_inspect_db_schema()
        elif tool_name == "get_provider_endpoints":
            return self.tool_get_provider_endpoints()
        elif tool_name == "inspect_system_health":
            return self.tool_inspect_system_health()
        else:
            return f"未知工具名稱：{tool_name}"

    # ---------------------------------------------------------
    # ReAct / Tool-Calling Reasoning Loop
    # ---------------------------------------------------------
    def _call_llm(self, messages: List[Dict[str, str]]) -> str:
        """Invokes active LLM provider chain."""
        chain = self.ai_provider_store.get_chain()
        local_url = self.ai_provider_store.get_local_base_url()

        for entry in chain:
            try:
                name, model = entry.split("/", 1) if "/" in entry else (entry, "")
                if name not in _PROVIDERS:
                    continue
                key = _provider_key(name, local_base_url=local_url)
                if not key:
                    continue
                model = _resolve_model(name, model, local_base_url=local_url)
                return _request_completion(name, key, model, messages, max_tokens=2500)
            except Exception as e:
                logger.warning(f"MaintenanceAgent LLM call failed with {entry}: {e}")
                continue

        raise RuntimeError("所有 AI Provider 均無法調用，無法進行維護模式診斷。")

    def ask(self, user_question: str) -> str:
        """
        Processes user natural language question through dynamic tool-calling.
        Returns detailed, diagnostic answer formatted in Telegram HTML.
        """
        tools_description = """
你可以調用以下安全唯讀的診斷工具來檢視底層真實數據與日誌：
1. inspect_stock(ticker: str) -> 查看特定個股在資料庫中的完整快照 (basic_info、financials、prices 等實體欄位)。
2. search_logs(keyword: str, max_lines: int) -> 搜尋 logs/gvw_bot.log 最近符合關鍵字的日誌。
3. inspect_db_schema() -> 查看 SQLite 資料庫表結構與記錄筆數。
4. get_provider_endpoints() -> 查看目前各資料源 (TWSE, FinMind, Yahoo) 呼叫的 API 端點及所提供的欄位。
5. inspect_system_health() -> 查看硬體、排程、心跳與連線健康報告。

【Action 協議規範】
當你需要調用工具查詢時，請只回傳以下 JSON 格式 (不要有其他文字)：
{
  "action": "<tool_name>",
  "args": {"<arg_name>": "<arg_value>"}
}

當你調用工具並獲得足夠的數據依據後，請回傳最終診斷結論，格式為：
{
  "action": "final_answer",
  "answer": "<你的繁體中文診斷分析與修復建議>"
}
"""

        system_prompt = (
            "你是 GVW 智能選股系統的首席維護與資料庫診斷專家 (Maintenance Copilot)。\n"
            "你的任務是協助系統負責人排查資料缺失、排程異常或管線問題。\n"
            "請基於真實資料說話，不可憑空猜測。必須使用工具查證系統與資料庫現況後再做結論。\n"
            + tools_description
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"負責人問題：{user_question}"},
        ]

        # Multi-turn tool execution loop (max 4 turns)
        for turn in range(4):
            logger.info(f"MaintenanceAgent turn {turn + 1}...")
            raw_response = self._call_llm(messages)

            # Parse JSON
            action_dict = None
            try:
                cleaned = raw_response.strip()
                if "```json" in cleaned:
                    cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
                elif "```" in cleaned:
                    cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
                action_dict = json.loads(cleaned)
            except Exception:
                pass

            if not isinstance(action_dict, dict) or "action" not in action_dict:
                # If LLM replied in plain text without JSON, treat as final answer
                return raw_response.strip()

            action = action_dict.get("action")
            if action == "final_answer":
                return action_dict.get("answer", raw_response).strip()

            # Execute tool
            args = action_dict.get("args", {})
            tool_res = self._execute_tool(action, args)
            tool_str = json.dumps(tool_res, ensure_ascii=False) if isinstance(tool_res, (dict, list)) else str(tool_res)

            # Truncate tool response if too large
            if len(tool_str) > 2000:
                tool_str = tool_str[:2000] + "... (truncated)"

            # Feed observation back to model
            messages.append({"role": "assistant", "content": json.dumps(action_dict, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"Observation from {action}:\n{tool_str}\n\n請根據以上真實觀測結果繼續分析。若資訊已足夠，請回傳 final_answer。"})

        return "⚠️ 已達到最大工具調用深度，未能完成診斷，請嘗試縮小問題範圍。"
