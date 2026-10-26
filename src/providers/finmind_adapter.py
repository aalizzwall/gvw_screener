import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, AsyncIterable, Dict, List, Optional

import requests

from src.interfaces.data_provider import DataProvider


class FinMindAdapter(DataProvider):
    """
    Adapter for FinMind Open Data API (https://finmindtrade.com).
    Supports token authentication via environment variable `FINMIND_API_TOKEN`
    or direct parameter, while also working under the free tier without a token.
    """

    BASE_URL = "https://api.finmindtrade.com/api/v4/data"

    def __init__(self, token: Optional[str] = None, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "[FINMIND_ADAPTER] %(asctime)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

        self.token = token or self._discover_token()

    def _discover_token(self) -> Optional[str]:
        for var in ("FINMIND_TOKEN", "FINMIND_API_TOKEN"):
            val = os.environ.get(var)
            if val:
                return val.strip()

        candidate_paths = [
            os.path.join(os.getcwd(), ".env"),
            "/home/pi/.hermes/.env",
        ]
        for env_path in candidate_paths:
            if os.path.exists(env_path):
                try:
                    with open(env_path, "r", encoding="utf-8") as env_file:
                        for line in env_file:
                            line = line.strip()
                            for var in ("FINMIND_TOKEN", "FINMIND_API_TOKEN"):
                                if line.startswith(f"{var}="):
                                    return line.split("=", 1)[1].strip().strip('"').strip("'")
                except OSError as exc:
                    self.logger.warning("Error reading %s: %s", env_path, exc)
        return None

    def validate_token(self) -> Dict[str, Any]:
        if not self.token:
            return {
                "configured": False,
                "tier": "guest",
                "valid": False,
                "message": "No FinMind token detected; using guest access.",
            }

        try:
            response = requests.get(
                self.BASE_URL,
                params={
                    "dataset": "TaiwanStockInfo",
                    "data_id": "2330",
                    "token": self.token,
                },
                timeout=10,
            )
            data = response.json()
            if response.status_code == 200 and data.get("status") == 200:
                masked = self.token[:6] + "..." + self.token[-4:] if len(self.token) > 10 else "***"
                return {
                    "configured": True,
                    "valid": True,
                    "tier": "registered",
                    "token_masked": masked,
                    "message": "FinMind token validation succeeded.",
                }
            return {
                "configured": True,
                "valid": False,
                "tier": "invalid",
                "error_msg": data.get("msg", response.text),
                "message": f"FinMind token validation failed (HTTP {response.status_code}).",
            }
        except (requests.RequestException, ValueError) as exc:
            return {
                "configured": True,
                "valid": False,
                "error": str(exc),
                "message": f"FinMind token validation failed: {exc}",
            }

    def _get_dataset(
        self,
        dataset: str,
        data_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"dataset": dataset, "data_id": data_id}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if self.token:
            params["token"] = self.token

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=20)
            if response.status_code == 429:
                self.logger.warning("FinMind API rate limit hit for %s:%s", dataset, data_id)
                return []
            response.raise_for_status()
            result = response.json()
            return result.get("data", [])
        except (requests.RequestException, ValueError) as exc:
            self.logger.error("Error fetching FinMind dataset %s for %s: %s", dataset, data_id, exc)
            return []

    def fetch_stock_data(self, ticker: str) -> Dict[str, Any]:
        self.logger.info("Fetching FinMind stock data for %s", ticker)
        start_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        fs_data = self._get_dataset(
            "TaiwanStockFinancialStatements", ticker, start_date=start_date
        )
        bs_data = self._get_dataset(
            "TaiwanStockBalanceSheet", ticker, start_date=start_date
        )
        per_data = self._get_dataset(
            "TaiwanStockPER",
            ticker,
            start_date=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        )
        info_data = self._get_dataset("TaiwanStockInfo", ticker)

        return {
            "ticker": ticker,
            "financials": {
                "income_statement": self._normalize_fs(fs_data),
                "balance_sheet": self._normalize_bs(bs_data),
            },
            "basic_info": self._normalize_info(info_data, per_data),
            "source": "finmind",
            "updated_at": datetime.now().isoformat(),
        }

    def _normalize_fs(self, fs_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not fs_data:
            return {}
        metrics: Dict[str, float] = {}
        for item in fs_data:
            metric_type = item.get("type")
            if metric_type:
                metrics[metric_type] = float(item.get("value", 0.0) or 0)

        res = {
            "revenue": metrics.get("Revenue", 0.0),
            "net_income": metrics.get(
                "IncomeAfterTaxes",
                metrics.get("TotalConsolidatedProfitForThePeriod", 0.0),
            ),
            "gross_profit": metrics.get("GrossProfit", 0.0),
            "operating_income": metrics.get("OperatingIncome", 0.0),
            "operating_expenses": metrics.get("OperatingExpenses", 0.0),
            "eps": metrics.get("EPS", 0.0),
        }

        fs_date = next((item.get("date") for item in fs_data if item.get("date")), None)
        if fs_date and len(str(fs_date)) >= 7:
            try:
                parts = str(fs_date)[:10].split("-")
                yr = int(parts[0])
                mo = int(parts[1])
                q_num = (mo - 1) // 3 + 1
                p_type = "單季" if q_num == 1 else "累計"
                res["date"] = str(fs_date)
                res["period"] = f"{yr}Q{q_num}"
                res["period_type"] = p_type
                res["period_label"] = f"{yr}Q{q_num} ({p_type})"
            except (ValueError, TypeError):
                pass

        return res

    def _normalize_bs(self, bs_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not bs_data:
            return {}
        metrics: Dict[str, float] = {}
        for item in bs_data:
            metric_type = item.get("type")
            if metric_type:
                metrics[metric_type] = float(item.get("value", 0.0) or 0)

        return {
            "total_assets": metrics.get("TotalAssets", 0.0),
            "total_liabilities": metrics.get("Liabilities", 0.0),
            "total_equity": metrics.get("Equity", 0.0),
        }

    def _normalize_info(
        self,
        info_data: List[Dict[str, Any]],
        per_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        info: Dict[str, Any] = {}
        if info_data:
            first = info_data[0]
            info["industry"] = first.get("industry_category", "")
            info["name"] = first.get("stock_name", "")
            info["market_type"] = first.get("type", "")
        if per_data:
            latest_per = per_data[-1]
            info["pe"] = latest_per.get("PER", 0.0)
            info["pb"] = latest_per.get("PBR", 0.0)
            info["yield"] = latest_per.get("dividend_yield", 0.0)
        return info

    def fetch_price_history(self, ticker: str, days: int = 365) -> List[Dict[str, Any]]:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        raw_prices = self._get_dataset("TaiwanStockPrice", ticker, start_date=start_date)
        return [
            {
                "date": price.get("date"),
                "open": float(price.get("open", 0)),
                "high": float(price.get("max", 0)),
                "low": float(price.get("min", 0)),
                "close": float(price.get("close", 0)),
                "volume": int(price.get("Trading_Volume", 0)),
            }
            for price in raw_prices
        ]

    def fetch_market_index(self, index_ticker: str = "TAIEX") -> Dict[str, Any]:
        self.logger.info("Fetching FinMind market index for %s", index_ticker)
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        data = self._get_dataset("TaiwanStockPrice", index_ticker, start_date=start_date)
        if not data:
            return {}

        latest = data[-1]
        return {
            "index_ticker": index_ticker,
            "date": latest.get("date"),
            "price": float(latest.get("close", 0)),
            "open": float(latest.get("open", 0)),
            "high": float(latest.get("max", 0)),
            "low": float(latest.get("min", 0)),
            "volume": int(latest.get("Trading_Volume", 0)),
            "updated_at": datetime.now().isoformat(),
        }

    def check_availability(self) -> bool:
        self.logger.info("Checking FinMind API availability...")
        return bool(self._get_dataset("TaiwanStockInfo", "2330"))

    async def stream_market_data(self, symbols: List[str]) -> AsyncIterable[Dict[str, Any]]:
        for symbol in symbols:
            prices = await asyncio.to_thread(self.fetch_price_history, symbol, 7)
            for price in prices:
                yield {"symbol": symbol, **price}

    async def stream_stock_data(self, ticker: str) -> AsyncIterable[Dict[str, Any]]:
        yield await asyncio.to_thread(self.fetch_stock_data, ticker)
