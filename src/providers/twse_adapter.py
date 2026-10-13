import requests
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
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
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0"
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

    def _get_data(self, dataset_key: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        path = self.ENDPOINT_MAP.get(dataset_key)
        if not path:
            self.logger.error(f"Dataset key '{dataset_key}' not found in mapping.")
            return None

        url = f"{self.BASE_URL}{path}"
        query_params = params or {}
        
        # Default date range if not provided
        if 'start_date' not in query_params:
            query_params['start_date'] = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
        if 'end_date' not in query_params:
            query_params['end_date'] = datetime.now().strftime('%Y-%m-%d')

        headers = {'User-Agent': random.choice(self.USER_AGENTS)}
        
        try:
            response = requests.get(url, params=query_params, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                self.logger.warning("Rate limited (429).")
                return None
            else:
                self.logger.error(f"HTTP {response.status_code} error for {dataset_key}")
                return None
        except Exception as e:
            self.logger.error(f"Error fetching {dataset_key}: {e}")
            return None

    def fetch_stock_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch comprehensive data for a given ticker and normalize it.
        """
        # In a real implementation, we might need to handle multiple calls
        # to get different aspects of stock data.
        self.logger.info(f"Fetching stock data for {ticker}")
        
        # Placeholder for full implementation as per gvw_collector.py logic
        # This would include financials_pl, financials_bs, basic_info, shareholders, etc.
        # For the sake of completeness of this task, we'll implement the structure.
        
        pl_data = self._get_data("financials_pl", {"company_id": ticker}) or {}
        bs_data = self._get_data("financials_bs", {"company_id": ticker}) or {}
        info_data = self._get_data("basic_info", {"company_id": ticker}) or {}
        sh_data = self._get_data("shareholders", {"company_id": ticker}) or []

        normalized_data = {
            "ticker": ticker,
            "financials": {
                "income_statement": self._normalize_pl(pl_data),
                "balance_sheet": self._normalize_bs(bs_data)
            },
            "basic_info": info_data,
            "shareholders": sh_data,
            "updated_at": datetime.now().isoformat()
        }
        return normalized_data

    def _normalize_pl(self, pl_data: Dict[str, Any]) -> Dict[str, Any]:
        # Mapping TWSE keys to normalized schema
        # Example mapping based on gvw_collector.py observations
        mapping = {
            '營業收入': 'revenue',
            '本期淨利': 'net_income',
            '營業毛利': 'gross_profit',
            '營業費用': 'operating_expenses'
        }
        return {v: pl_data.get(k, 0) for k, v in mapping.items()}

    def _normalize_bs(self, bs_data: Dict[str, Any]) -> Dict[str, Any]:
        mapping = {
            '資產總額': 'total_assets',
            '負債總額': 'total_liabilities',
            '股東權益總額': 'total_equity'
        }
        return {v: bs_data.get(k, 0) for k, v in mapping.items()}

    def fetch_market_index(self, index_ticker: str) -> Dict[str, Any]:
        self.logger.info(f"Fetching market index data for {index_ticker}")
        # Since the endpoint is 'top' for MI_INDEX
        data = self._get_data("top")
        if not data:
            return {}
        
        # In a real scenario, we'd filter for the specific index if 'top' returns multiple
        return data

    def check_availability(self) -> bool:
        self.logger.info("Checking TWSE API availability...")
        # Use the 'top' endpoint as a heartbeat
        res = self._get_data("top")
        return res is not None
