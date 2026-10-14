import logging
from typing import Dict, Any, List, Optional
from src.providers.twse_adapter import TWSEAdapter
from src.providers.finmind_adapter import FinMindAdapter
from src.providers.yahoo_adapter import YahooAdapter

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

    def fetch_all_data(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Attempts to fetch fundamental/financial data across available providers.
        """
        # 1. Try TWSE
        try:
            data = self.twse.fetch_stock_data(ticker)
            if data and not data.get('error'):
                return data
        except Exception as e:
            logger.warning(f"[FACTORY] TWSE failed for {ticker}: {e}")

        # 2. Try FinMind
        if self.finmind:
            try:
                data = self.finmind.fetch_stock_data(ticker)
                if data and not data.get('error') and data.get('financials'):
                    return data
            except Exception as e:
                logger.warning(f"[FACTORY] FinMind failed for {ticker}: {e}")

        # 3. Fallback to Yahoo Finance
        try:
            data = self.yahoo.fetch_stock_data(ticker)
            if data and not data.get('error'):
                return data
        except Exception as e:
            logger.error(f"[FACTORY] Yahoo failed for {ticker}: {e}")

        return None

    def fetch_price_history(self, ticker: str, days: int = 365) -> List[Dict[str, Any]]:
        """
        Fetch historical price/K-line data.
        Tries FinMind first, falls back to Yahoo Finance.
        """
        # 1. Try FinMind
        try:
            prices = self.finmind.fetch_price_history(ticker, days=days)
            if prices:
                return prices
        except Exception as e:
            logger.warning(f"[FACTORY] FinMind price history failed for {ticker}: {e}")

        # 2. Fallback to Yahoo Finance
        try:
            import yfinance as yf
            import pandas as pd
            symbol = f"{ticker}.TW" if ticker.isdigit() else ticker
            df = yf.Ticker(symbol).history(period=f"{days}d", interval="1d")
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                return [
                    {
                        'date': date.strftime('%Y-%m-%d'),
                        'open': float(row['Open']),
                        'high': float(row['High']),
                        'low': float(row['Low']),
                        'close': float(row['Close']),
                        'volume': int(row['Volume'])
                    }
                    for date, row in df.iterrows()
                ]
        except Exception as e:
            logger.error(f"[FACTORY] Yahoo price history failed for {ticker}: {e}")

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

