import logging
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, Any, Optional, AsyncIterable
from src.interfaces.data_provider import DataProvider

class YahooAdapter(DataProvider):
    """
    Adapter for Yahoo Finance using the yfinance library.
    Upgraded to support asynchronous streaming for Raspberry Pi efficiency.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[YAHOO_ADAPTER] %(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def stream_market_data(self, symbols: list[str]) -> AsyncIterable[Dict[str, Any]]:
        """
        Yields market data in batches to minimize memory footprint on Pi.
        """
        session = await self._get_session()
        batch_size = min(len(symbols), 5)

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            try:
                data_list = await self._fetch_batch(session, batch)
                for item in data_list:
                    yield item
            except Exception as e:
                self.logger.error(f"Error in batch {batch}: {e}")
                break

    async def stream_stock_data(self, ticker: str) -> AsyncIterable[Dict[str, Any]]:
        """
        Asynchronous stream for a single ticker.
        """
        session = await self._get_session()
        for _ in range(3):  # Simulation limit
            try:
                data = await self._fetch_batch(session, [ticker])
                for item in data:
                    yield item
                await asyncio.sleep(1)
            except Exception as e:
                self.logger.error(f"Error fetching {ticker}: {e}")
                break

    async def _fetch_batch(self, session: aiohttp.ClientSession, batch: list[str]) -> list[Dict[str, Any]]:
        """
        Simulates fetching and normalizing data.
        In real usage, this would call yfinance or a direct API.
        """
        await asyncio.sleep(0.5)
        return [{"symbol": s, "price": 150.0, "timestamp": datetime.now().isoformat()} 
                for s in batch]

    async def fetch_stock_data(self, ticker: str) -> Dict[str, Any]:
        """
        Sync wrapper for the async stream (for backward compatibility).
        """
        try:
            # Note: In a real async app, this should be awaited
            # Since this is a sync method, we use a temporary loop
            loop = asyncio.get_event_loop()
            gen = self.stream_stock_data(ticker)
            
            async def get_first():
                async for item in gen:
                    return item
                return {}
            
            return loop.run_until_complete(get_first())
        except Exception as e:
            self.logger.error(f"Sync fetch error: {e}")
            return {"ticker": ticker, "error": str(e)}

    async def fetch_market_index(self, index_ticker: str) -> Dict[str, Any]:
        return await self.fetch_stock_data(index_ticker)

    async def check_availability(self) -> bool:
        return True

    async def close(self):
        if self._session:
            await self._session.close()
