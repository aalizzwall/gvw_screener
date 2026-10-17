import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, AsyncIterable, Dict, List, Optional

import aiohttp

from src.interfaces.data_provider import DataProvider


class YahooAdapter(DataProvider):
    """Fetch real stock data from Yahoo Finance."""

    CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)

    @staticmethod
    def _symbols(ticker: str) -> List[str]:
        if ticker.isdigit():
            return [f"{ticker}.TW", f"{ticker}.TWO"]
        return [ticker]

    @staticmethod
    def _number(value: Any) -> float:
        try:
            number = float(value)
            return number if math.isfinite(number) else 0.0
        except (TypeError, ValueError):
            return 0.0

    def fetch_price_history(self, ticker: str, days: int = 365) -> List[Dict[str, Any]]:
        import yfinance as yf

        for symbol in self._symbols(ticker):
            try:
                history = yf.Ticker(symbol).history(
                    period=f"{max(days, 1)}d",
                    interval="1d",
                    auto_adjust=False,
                )
            except Exception as exc:
                self.logger.warning("Yahoo price history failed for %s: %s", symbol, exc)
                continue

            if history is None or history.empty:
                continue

            prices = []
            for date, row in history.iterrows():
                close = self._number(row.get("Close"))
                if close <= 0:
                    continue
                prices.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "open": self._number(row.get("Open")),
                    "high": self._number(row.get("High")),
                    "low": self._number(row.get("Low")),
                    "close": close,
                    "volume": int(self._number(row.get("Volume"))),
                    "source": "yahoo_finance",
                })

            if prices:
                return sorted(prices, key=lambda item: item["date"])
        return []

    def fetch_stock_data(self, ticker: str) -> Dict[str, Any]:
        import yfinance as yf

        for symbol in self._symbols(ticker):
            try:
                info = yf.Ticker(symbol).info
            except Exception as exc:
                self.logger.warning("Yahoo stock data failed for %s: %s", symbol, exc)
                continue

            if not isinstance(info, dict) or not any(
                info.get(key) for key in ("longName", "shortName", "totalRevenue", "trailingEps")
            ):
                continue

            dividend_yield = self._number(info.get("dividendYield")) * 100
            return {
                "ticker": ticker,
                "source": "yahoo_finance",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "basic_info": {
                    "name": info.get("longName") or info.get("shortName", ""),
                    "industry": info.get("industry", ""),
                    "market_type": info.get("quoteType", ""),
                    "pe": self._number(info.get("trailingPE")),
                    "pb": self._number(info.get("priceToBook")),
                    "yield": round(dividend_yield, 4),
                    "roe": round(self._number(info.get("returnOnEquity")) * 100, 2),
                },
                "financials": {
                    "income_statement": {
                        "revenue": self._number(info.get("totalRevenue")),
                        "net_income": self._number(info.get("netIncomeToCommon")),
                        "gross_profit": self._number(info.get("grossProfits")),
                        "operating_income": self._number(info.get("operatingIncome")),
                        "operating_expenses": self._number(info.get("operatingExpenses")),
                        "eps": self._number(info.get("trailingEps")),
                    },
                    "balance_sheet": {
                        "total_assets": self._number(info.get("totalAssets")),
                        "total_liabilities": self._number(info.get("totalLiabilities")),
                        "total_equity": self._number(info.get("totalStockholderEquity")),
                    },
                },
                "shareholders": [],
            }

        return {"ticker": ticker, "source": "yahoo_finance", "error": "No usable Yahoo Finance data"}

    async def stream_market_data(self, symbols: list[str]) -> AsyncIterable[Dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for start in range(0, len(symbols), 5):
                for item in await self._fetch_batch(session, symbols[start:start + 5]):
                    yield item

    async def _fetch_batch(
        self,
        session: aiohttp.ClientSession,
        batch: list[str],
    ) -> List[Dict[str, Any]]:
        async def fetch_latest(symbol: str) -> Optional[Dict[str, Any]]:
            try:
                async with session.get(
                    self.CHART_URL.format(symbol=symbol),
                    params={"range": "5d", "interval": "1d"},
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                result = payload.get("chart", {}).get("result")
                if not result:
                    return None
                chart = result[0]
                timestamps = chart.get("timestamp") or []
                closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                for timestamp, close in reversed(list(zip(timestamps, closes))):
                    price = self._number(close)
                    if price > 0:
                        return {
                            "symbol": symbol,
                            "price": price,
                            "timestamp": datetime.fromtimestamp(
                                timestamp, tz=timezone.utc
                            ).isoformat(),
                            "source": "yahoo_finance",
                        }
                return None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, IndexError) as exc:
                self.logger.warning("Yahoo market data failed for %s: %s", symbol, exc)
                return None

        return [item for item in await asyncio.gather(*(fetch_latest(s) for s in batch)) if item]

    async def stream_stock_data(self, ticker: str) -> AsyncIterable[Dict[str, Any]]:
        data = await asyncio.to_thread(self.fetch_stock_data, ticker)
        if not data.get("error"):
            yield data

    def fetch_market_index(self, index_ticker: str) -> Dict[str, Any]:
        prices = self.fetch_price_history(index_ticker, days=5)
        if not prices:
            return {}
        latest = prices[-1]
        return {
            "ticker": index_ticker,
            "price": latest["close"],
            "updated_at": latest["date"],
            "source": latest["source"],
        }

    def check_availability(self) -> bool:
        try:
            return bool(self.fetch_price_history("^TWII", days=5))
        except Exception as exc:
            self.logger.warning("Yahoo availability check failed: %s", exc)
            return False
