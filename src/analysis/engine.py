from abc import ABC, abstractmethod
from typing import AsyncIterable, Dict, Any, Protocol

class ScoredResult(Protocol):
    """
    定義評估結果的結構
    """
    ticker: str
    score: float
    metadata: Dict[str, Any]

class GVWEngine(ABC):
    """
    GVW 選股引擎介面 (The Seam)
    """

    @abstractmethod
    async def process_stream(self, data_stream: AsyncIterable[Dict[str, Any]]) -> AsyncIterable[Dict[str, Any]]:
        """
        接收市場數據流並輸出評估後的結果流。
        
        Args:
            data_stream: 來自 MarketDataProvider 的數據流。
            
        Yields:
            包含 ticker, score 與 metadata 的字典。
        """
        pass

    @abstractmethod
    def update_strategy(self, strategy_params: Dict[str, Any]):
        """
        動態更新評估策略參數。
        """
        pass
