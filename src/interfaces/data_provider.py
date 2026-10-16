from abc import ABC, abstractmethod
from typing import AsyncIterable, Dict, Any

class MarketDataProvider(ABC):
    """
    市場數據供應商介面 (The Seam)
    """
    
    @abstractmethod
    async def stream_market_data(self, symbols: list[str]) -> AsyncIterable[Dict[str, Any]]:
        """
        以流式 (Streaming) 方式提供市場數據。
        
        Args:
            symbols: 需要監控的標的清單。
            
        Yields:
            包含標的價格與時間戳的優化後的字典。
        """
        pass

class DataProvider(MarketDataProvider):
    """
    原有介面的相容版本，將其轉化為支援流式處理的深層介面。
    """

    @abstractmethod
    async def stream_stock_data(self, ticker: str) -> AsyncIterable[Dict[str, Any]]:
        """
        以流式方式獲取單一標的的全面數據。
        """
        pass

