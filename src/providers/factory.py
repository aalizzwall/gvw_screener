import logging
import math
import requests
from typing import Dict, Any, List, Optional
from src.providers.twse_adapter import TWSEAdapter
from src.providers.finmind_adapter import FinMindAdapter
from src.providers.yahoo_adapter import TickerNotFoundError, YahooAdapter

logger = logging.getLogger(__name__)

class DataProviderFactory:
    """
    Factory to manage multiple DataProviders with automatic failover logic:
    TWSE (Primary) -> FinMind (Secondary) -> Yahoo Finance (Fallback)
    """

    def __init__(
        self,
        twse_adapter: Optional[TWSEAdapter] = None,
        finmind_adapter: Optional[FinMindAdapter] = None,
        yahoo_adapter: Optional[YahooAdapter] = None
    ):
        self.twse = twse_adapter or TWSEAdapter()
        self.finmind = finmind_adapter or FinMindAdapter()
        self.yahoo = yahoo_adapter or YahooAdapter()

    def fetch_universe(self) -> Dict[str, str]:
        sources = (
            ("TWSE", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"),
            ("TPEx", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"),
        )
        universe = {}
        for exchange, url in sources:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"{exchange} returned an empty or invalid stock list")

            exchange_universe = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ticker = next(
                    (str(row[key]).strip() for key in (
                        "公司代號", "公司代碼", "SecuritiesCompanyCode", "CompanyId",
                    ) if row.get(key)),
                    "",
                )
                name = next(
                    (str(row[key]).strip() for key in (
                        "公司名稱", "公司簡稱", "CompanyName",
                    ) if row.get(key)),
                    "",
                )
                if ticker.isdigit() and name:
                    exchange_universe[ticker] = name

            if not exchange_universe:
                raise ValueError(f"{exchange} stock list contained no usable entries")
            universe.update(exchange_universe)

        return universe

    @staticmethod
    def _has_stock_data(data: Any) -> bool:
        if not isinstance(data, dict) or data.get("error"):
            return False

        def has_numeric_value(value: Any) -> bool:
            if isinstance(value, dict):
                return any(has_numeric_value(item) for item in value.values())
            if isinstance(value, (list, tuple)):
                return any(has_numeric_value(item) for item in value)
            if isinstance(value, (int, float)):
                return math.isfinite(value) and value != 0
            if isinstance(value, str):
                try:
                    number = float(value.replace(",", "").strip())
                    return math.isfinite(number) and number != 0
                except ValueError:
                    return False
            return False

        if has_numeric_value(data.get("financials", {})):
            return True
        basic_info = data.get("basic_info", {})
        return isinstance(basic_info, dict) and any(
            has_numeric_value(basic_info.get(key))
            for key in ("roe", "pb", "pe", "yield", "price", "close")
        )

    @staticmethod
    def _valid_price_history(prices: Any, source: str) -> List[Dict[str, Any]]:
        if not isinstance(prices, list):
            return []

        valid = []
        for item in prices:
            if not isinstance(item, dict) or not item.get("date"):
                continue
            try:
                close = float(item.get("close"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(close) or close <= 0:
                continue
            valid.append({**item, "close": close, "source": item.get("source") or source})
        return sorted(valid, key=lambda item: str(item["date"]))

    def fetch_all_data(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Attempts to fetch fundamental/financial data across available providers.
        """
        providers = (
            ("TWSE", self.twse),
            ("FinMind", self.finmind),
            ("Yahoo Finance", self.yahoo),
        )
        for name, provider in providers:
            try:
                data = provider.fetch_stock_data(ticker)
                if self._has_stock_data(data):
                    return data
                logger.warning("[FACTORY] %s returned no usable stock data for %s", name, ticker)
            except Exception as e:
                logger.warning("[FACTORY] %s failed for %s: %s", name, ticker, e)

        return None

    def fetch_price_history(self, ticker: str, days: int = 365) -> List[Dict[str, Any]]:
        """
        Fetch historical price/K-line data.
        Tries FinMind first, falls back to Yahoo Finance.
        """
        yahoo_not_found = None
        for name, provider in (("FinMind", self.finmind), ("Yahoo Finance", self.yahoo)):
            try:
                prices = self._valid_price_history(
                    provider.fetch_price_history(ticker, days=days),
                    "finmind" if name == "FinMind" else "yahoo_finance",
                )
                if prices:
                    return prices
                logger.warning("[FACTORY] %s returned no usable prices for %s", name, ticker)
            except TickerNotFoundError as e:
                yahoo_not_found = e
                logger.warning("[FACTORY] Yahoo confirmed missing market symbols for %s", ticker)
            except Exception as e:
                logger.warning("[FACTORY] %s price history failed for %s: %s", name, ticker, e)

        if yahoo_not_found:
            raise yahoo_not_found
        return []

    def fetch_market_index(self, index_ticker: str = "TWII") -> Dict[str, Any]:
        """
        Fetch market index data.
        """
        # 1. Try TWSE
        try:
            stats = self.twse.fetch_market_index(index_ticker)
            if stats:
                return stats
        except Exception as e:
            logger.warning(f"[FACTORY] TWSE market index failed: {e}")

        # 2. Try FinMind
        try:
            finmind_idx = "TAIEX" if index_ticker in ["TWII", "^TWII", "TAIEX"] else index_ticker
            idx_data = self.finmind.fetch_market_index(finmind_idx)
            if idx_data:
                return idx_data
        except Exception as e:
            logger.warning(f"[FACTORY] FinMind market index failed: {e}")

        # 3. Fallback to Yahoo
        try:
            yf_ticker = "^TWII" if index_ticker == "TWII" else index_ticker
            return self.yahoo.fetch_market_index(yf_ticker)
        except Exception as e:
            logger.error(f"[FACTORY] Yahoo market index failed: {e}")

        return {}

    def check_availability(self) -> Dict[str, bool]:
        """
        Check health/connectivity of all underlying providers.
        """
        return {
            "twse": self.twse.check_availability(),
            "finmind": self.finmind.check_availability(),
            "yahoo": self.yahoo.check_availability()
        }
