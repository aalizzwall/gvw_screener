import os
import requests
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
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
            formatter = logging.Formatter('[FINMIND_ADAPTER] %(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

        self.token = token or self._discover_token()

    def _discover_token(self) -> Optional[str]:
        # 1. Environment variables
        for var in ["FINMIND_TOKEN", "FINMIND_API_TOKEN"]:
            val = os.environ.get(var)
            if val:
                return val.strip()

        # 2. Check local .env or fallback paths
        candidate_paths = [
            os.path.join(os.getcwd(), ".env"),
            "/home/pi/.hermes/.env"
        ]
        for env_path in candidate_paths:
            if os.path.exists(env_path):
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            for var in ["FINMIND_TOKEN", "FINMIND_API_TOKEN"]:
                                if line.startswith(f"{var}="):
                                    return line.split("=", 1)[1].strip().strip('"').strip("'")
                except Exception as e:
                    self.logger.warning(f"Error reading {env_path}: {e}")

        return None

    def validate_token(self) -> Dict[str, Any]:
        """
        Verify if the configured token is valid and determine tier status.
        """
        if not self.token:
            return {
                "configured": False,
                "tier": "guest (無 Token，約 300 次/小時)",
                "valid": False,
                "message": "未檢測到 Token，目前處於訪客/未驗證模式。"
            }

        try:
            resp = requests.get(
                self.BASE_URL,
                params={"dataset": "TaiwanStockInfo", "data_id": "2330", "token": self.token},
                timeout=10
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("status") == 200:
                masked = self.token[:6] + "..." + self.token[-4:] if len(self.token) > 10 else "***"
                return {
                    "configured": True,
                    "valid": True,
                    "tier": "registered (註冊會員等級，600 次/小時)",
                    "token_masked": masked,
                    "message": "Token 驗證成功，具備註冊等級權限（每小時 600 次請求限額）。"
                }
            else:
                return {
                    "configured": True,
                    "valid": False,
                    "tier": "invalid",
                    "error_msg": data.get("msg", resp.text),
                    "message": f"Token 驗證失敗：{data.get('msg', 'HTTP ' + str(resp.status_code))}"
                }
        except Exception as e:
            return {
                "configured": True,
                "valid": False,
                "error": str(e),
                "message": f"驗證請求時發生異常：{e}"
            }

    def _get_dataset(self, dataset: str, data_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {
            "dataset": dataset,
            "data_id": data_id
        }
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if self.token:
            params["token"] = self.token

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=20)
            if response.status_code == 200:
                result = response.json()
                return result.get("data", [])
            elif response.status_code == 429:
                self.logger.warning(f"FinMind API rate limit hit (429) for {dataset}:{data_id}")
                return []
            else:
                self.logger.error(f"FinMind HTTP {response.status_code} for {dataset}:{data_id}")
                return []
        except Exception as e:
            self.logger.error(f"Error fetching FinMind dataset {dataset} for {data_id}: {e}")
            return []

    def fetch_stock_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch normalized fundamental, valuation, and financial statement data.
        """
        self.logger.info(f"Fetching FinMind stock data for {ticker}")
        start_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')

        # 1. Financial Statements (Income Statement)
        fs_data = self._get_dataset("TaiwanStockFinancialStatements", ticker, start_date=start_date)
        
        # 2. Balance Sheet
        bs_data = self._get_dataset("TaiwanStockBalanceSheet", ticker, start_date=start_date)

        # 3. Valuation (PER, PBR, Dividend Yield)
        per_data = self._get_dataset("TaiwanStockPER", ticker, start_date=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))

        # 4. Stock Basic Info
        info_data = self._get_dataset("TaiwanStockInfo", ticker)

        normalized_data = {
            "ticker": ticker,
            "financials": {
                "income_statement": self._normalize_fs(fs_data),
                "balance_sheet": self._normalize_bs(bs_data)
            },
            "basic_info": self._normalize_info(info_data, per_data),
            "source": "finmind",
            "updated_at": datetime.now().isoformat()
        }
        return normalized_data

    def _normalize_fs(self, fs_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract latest income statement metrics."""
        if not fs_data:
            return {}
        # Group by type and get most recent entry
        metrics: Dict[str, float] = {}
        for item in fs_data:
            metric_type = item.get("type")
            value = item.get("value", 0.0)
            if metric_type:
                metrics[metric_type] = float(value or 0)

        return {
            "revenue": metrics.get("Revenue", 0.0),
            "net_income": metrics.get("IncomeAfterTaxes", metrics.get("TotalConsolidatedProfitForThePeriod", 0.0)),
            "gross_profit": metrics.get("GrossProfit", 0.0),
            "operating_income": metrics.get("OperatingIncome", 0.0),
            "operating_expenses": metrics.get("OperatingExpenses", 0.0),
            "eps": metrics.get("EPS", 0.0)
        }

    def _normalize_bs(self, bs_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract latest balance sheet metrics."""
        if not bs_data:
            return {}
        metrics: Dict[str, float] = {}
        for item in bs_data:
            metric_type = item.get("type")
            value = item.get("value", 0.0)
            if metric_type:
                metrics[metric_type] = float(value or 0)

        return {
            "total_assets": metrics.get("TotalAssets", 0.0),
            "total_liabilities": metrics.get("Liabilities", 0.0),
            "total_equity": metrics.get("Equity", 0.0)
        }

    def _normalize_info(self, info_data: List[Dict[str, Any]], per_data: List[Dict[str, Any]]) -> Dict[str, Any]:
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
        """
        Fetch historical daily prices (K-line data).
        """
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        raw_prices = self._get_dataset("TaiwanStockPrice", ticker, start_date=start_date)
        
        normalized_prices = []
        for p in raw_prices:
            normalized_prices.append({
                "date": p.get("date"),
                "open": float(p.get("open", 0)),
                "high": float(p.get("max", 0)),
                "low": float(p.get("min", 0)),
                "close": float(p.get("close", 0)),
                "volume": int(p.get("Trading_Volume", 0))
            })
        return normalized_prices

    def fetch_market_index(self, index_ticker: str = "TAIEX") -> Dict[str, Any]:
        """
        Fetch market index data (default 'TAIEX' for TWSE TAIEX index).
        """
        self.logger.info(f"Fetching FinMind market index for {index_ticker}")
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
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
            "updated_at": datetime.now().isoformat()
        }

    def check_availability(self) -> bool:
        """
        Check if FinMind API is responsive.
        """
        self.logger.info("Checking FinMind API availability...")
        try:
            data = self._get_dataset("TaiwanStockInfo", "2330")
            return len(data) > 0
        except Exception as e:
            self.logger.error(f"FinMind availability check failed: {e}")
            return False

