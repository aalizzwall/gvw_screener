import asyncio
import requests
import logging
import math
import random
from datetime import datetime
from typing import Dict, Any, List, Optional, AsyncIterable, Union
from src.interfaces.data_provider import DataProvider

class TWSEAdapter(DataProvider):
    """
    Adapter for the TWSE (Taiwan Stock Exchange) OpenAPI.
    """

    ENDPOINT_MAP = {
        "top": "/exchangeReport/MI_INDEX",           # Market Index / Overall Stats
        "financials_pl": "/opendata/t187ap06_L_ci",  # Income Statement (General)
        "financials_bs": "/opendata/t187ap07_L_ci",  # Balance Sheet (General)
        "daily_all": "/exchangeReport/STOCK_DAY_ALL", # Daily Trading Info
        "basic_info": "/opendata/t187ap03_L",        # Company Basic Info (Industry)
        "shareholders": "/opendata/t187ap02_L",      # Major Shareholders (>10%)
    }

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    ]

    BASE_URL = "https://openapi.twse.com.tw/v1"

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[TWSE_ADAPTER] %(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def _get_data(
        self,
        dataset_key: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Union[Dict[str, Any], List[Dict[str, Any]]]]:
        path = self.ENDPOINT_MAP.get(dataset_key)
        if not path:
            self.logger.error(f"Dataset key '{dataset_key}' not found in mapping.")
            return None

        url = f"{self.BASE_URL}{path}"
        headers = {'User-Agent': random.choice(self.USER_AGENTS)}
        
        try:
            response = requests.get(url, params=params or {}, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, (dict, list)) and data:
                    return data
                self.logger.warning("Empty or invalid response for TWSE dataset %s", dataset_key)
                return None
            elif response.status_code == 429:
                self.logger.warning("Rate limited (429).")
                return None
            else:
                self.logger.error(f"HTTP {response.status_code} error for {dataset_key}")
                return None
        except (requests.RequestException, ValueError) as e:
            self.logger.error(f"Error fetching {dataset_key}: {e}")
            return None

    @staticmethod
    def _select_company_record(data: Any, ticker: str) -> Dict[str, Any]:
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, list):
                return TWSEAdapter._select_company_record(nested, ticker)
            if not data:
                return {}
            code_keys = ("公司代號", "公司代碼", "CompanyId", "company_id", "ticker", "stock_id")
            code = next((data[key] for key in code_keys if key in data), None)
            if code is None or str(code).strip() == ticker:
                return data
            return {}

        if isinstance(data, list):
            code_keys = ("公司代號", "公司代碼", "CompanyId", "company_id", "ticker", "stock_id")
            for row in data:
                if not isinstance(row, dict):
                    continue
                code = next((row[key] for key in code_keys if key in row), None)
                if code is not None and str(code).strip() == ticker:
                    return row
        return {}

    def fetch_stock_data(self, ticker: str) -> Dict[str, Any]:
        self.logger.info(f"Fetching stock data for {ticker}")
        
        pl_data = self._select_company_record(
            self._get_data("financials_pl", {"company_id": ticker}), ticker
        )
        bs_data = self._select_company_record(
            self._get_data("financials_bs", {"company_id": ticker}), ticker
        )
        info_data = self._select_company_record(
            self._get_data("basic_info", {"company_id": ticker}), ticker
        )
        sh_data = self._get_data("shareholders", {"company_id": ticker}) or []
        if isinstance(sh_data, list):
            sh_data = [
                row for row in sh_data
                if isinstance(row, dict)
                and str(row.get("公司代號", row.get("CompanyId", ticker))).strip() == ticker
            ]
        elif isinstance(sh_data, dict):
            sh_data = self._select_company_record(sh_data, ticker)
        else:
            sh_data = []

        if not any((pl_data, bs_data, info_data, sh_data)):
            return {"ticker": ticker, "source": "twse", "error": "No TWSE data for ticker"}

        normalized_data = {
            "ticker": ticker,
            "source": "twse",
            "financials": {
                "income_statement": self._normalize_pl(pl_data),
                "balance_sheet": self._normalize_bs(bs_data)
            },
            "basic_info": info_data,
            "shareholders": sh_data,
            "updated_at": datetime.now().isoformat()
        }
        return normalized_data

    @staticmethod
    def _number(value: Any) -> float:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        try:
            number = float(value) if value not in (None, "") else 0.0
            return number if math.isfinite(number) else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _normalize_pl(self, pl_data: Dict[str, Any]) -> Dict[str, Any]:
        mapping = {
            "revenue": ("營業收入",),
            "net_income": ("本期淨利（淨損）", "繼續營業單位本期淨利（淨損）", "本期淨利"),
            "gross_profit": ("營業毛利（毛損）淨額", "營業毛利（毛損）", "營業毛利"),
            "operating_expenses": ("營業費用",),
            "operating_income": ("營業利益（損失）", "營業利益"),
            "eps": ("基本每股盈餘（元）", "每股盈餘"),
        }
        return {
            target: self._number(next(
                (pl_data[key] for key in aliases if pl_data.get(key) not in (None, "")),
                0,
            ))
            for target, aliases in mapping.items()
        } if isinstance(pl_data, dict) else {}

    def _normalize_bs(self, bs_data: Dict[str, Any]) -> Dict[str, Any]:
        mapping = {
            "total_assets": ("資產總計", "資產總額"),
            "total_liabilities": ("負債總計", "負債總額"),
            "total_equity": ("權益總計", "股東權益總額", "歸屬於母公司業主之權益合計"),
        }
        return {
            target: self._number(next(
                (bs_data[key] for key in aliases if bs_data.get(key) not in (None, "")),
                0,
            ))
            for target, aliases in mapping.items()
        } if isinstance(bs_data, dict) else {}

    def fetch_market_index(self, index_ticker: str) -> Dict[str, Any]:
        self.logger.info(f"Fetching market index data for {index_ticker}")
        data = self._get_data("top")
        if not data:
            return {}
        return data

    def check_availability(self) -> bool:
        self.logger.info("Checking TWSE API availability...")
        res = self._get_data("top")
        return res is not None

    async def stream_market_data(self, symbols: list[str]) -> AsyncIterable[Dict[str, Any]]:
        """
        Implementation of required async method for DataProvider.
        """
        for symbol in symbols:
            data = await asyncio.to_thread(self.fetch_market_index, symbol)
            yield {"symbol": symbol, "data": data}

    async def stream_stock_data(self, ticker: str) -> AsyncIterable[Dict[str, Any]]:
        """
        Implementation of required async method for DataProvider.
        """
        yield await asyncio.to_thread(self.fetch_stock_data, ticker)
