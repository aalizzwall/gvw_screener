import logging
import yfinance as yf
from datetime import datetime
from typing import Dict, Any, Optional
from src.interfaces.data_provider import DataProvider

class YahooAdapter(DataProvider):
    """
    Adapter for Yahoo Finance using the yfinance library.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[YAHOO_ADAPTER] %(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def fetch_stock_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch comprehensive data for a given ticker and normalize it.
        Handles ticker symbol formatting for Taiwan stocks.
        """
        symbol = f"{ticker}.TW" if ticker.isdigit() else ticker
        self.logger.info(f"Fetching Yahoo Finance data for {symbol}")
        
        try:
            yf_ticker = yf.Ticker(symbol)
            info = yf_ticker.info
            
            if not info or 'regularMarketPrice' not in info and 'currentPrice' not in info:
                self.logger.warning(f"No significant data found for {symbol}")
                return {"ticker": ticker, "error": "No data found", "updated_at": datetime.now().isoformat()}

            normalized_data = {
                "ticker": ticker,
                "financials": {
                    "income_statement": self._normalize_financials(info),
                    "balance_sheet": self._normalize_balance_sheet(info)
                },
                "basic_info": self._normalize_info(info),
                "updated_at": datetime.now().isoformat()
            }
            return normalized_data
        except Exception as e:
            self.logger.error(f"Error fetching data for {symbol}: {e}")
            return {"ticker": ticker, "error": str(e), "updated_at": datetime.now().isoformat()}

    def _normalize_financials(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Maps yfinance attributes to the internal normalized schema.
        """
        # Standard mapping based on common yfinance keys
        return {
            "revenue": info.get("totalRevenue", 0),
            "net_income": info.get("netIncomeToCommon", 0),
            "gross_profit": info.get("grossProfit", 0),
            "operating_income": info.get("operatingIncome", 0)
        }

    def _normalize_balance_sheet(self, info: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "total_assets": info.get("totalAssets", 0),
            "total_liabilities": info.get("totalLiabilities", 0),
            "total_equity": info.get("totalStockholderEquity", 0),
            "debt_to_equity": info.get("debtToEquity", 0)
        }

    def _normalize_info(self, info: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "price_to_book": info.get("priceToBook", 0),
            "trailing_pe": info.get("trailingPE", 0),
            "dividend_yield": info.get("dividendYield", 0),
            "market_cap": info.get("marketCap", 0)
        }

    def fetch_market_index(self, index_ticker: str) -> Dict[str, Any]:
        """
        Fetch market index data (e.g., '^TWII').
        """
        self.logger.info(f"Fetching market index data for {index_ticker}")
        try:
            yf_ticker = yf.Ticker(index_ticker)
            info = yf_ticker.info
            return {
                "index_ticker": index_ticker,
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "updated_at": datetime.now().isoformat()
            }
        except Exception as e:
            self.logger.error(f"Error fetching index {index_ticker}: {e}")
            return {"index_ticker": index_ticker, "error": str(e)}

    def check_availability(self) -> bool:
        """
        Check connectivity/availability of yfinance.
        """
        try:
            # Attempt a small fetch to verify connectivity
            test_ticker = yf.Ticker("^TWII")
            _ = test_ticker.info
            return True
        except Exception as e:
            self.logger.error(f"Yahoo availability check failed: {e}")
            return False