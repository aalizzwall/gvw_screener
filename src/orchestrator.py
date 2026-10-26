import logging
import asyncio
from typing import List, Dict, Any, Optional
from src.interfaces.data_provider import MarketDataProvider
from src.interfaces.repository import DataRepository
from src.analysis.engine import GVWEngine

class Orchestrator:
    """
    Orchestrator connects the Market Data Provider (Adapter) with the GVW Engine.
    """
    def __init__(
        self, 
        data_provider: MarketDataProvider, 
        engine: GVWEngine,
        repository: Optional[DataRepository] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.data_provider = data_provider
        self.engine = engine
        self.repository = repository

    async def run(self, symbols: List[str]) -> None:
        """
        Starts the data stream from the Adapter and pipes it into the GVW Engine.
        """
        self.logger.info(f"Starting orchestration for symbols: {symbols}")
        
        try:
            # 1. Initialize/Start the data stream from the Adapter
            data_stream = self.data_provider.stream_market_data(symbols)
            
            # 2. Pipe the stream into the GVW Engine
            scored_stream = self.engine.process_stream(data_stream)
            
            # 3. Output the resulting scored items
            async for scored_item in scored_stream:
                self.logger.info(f"Scored Item: {scored_item}")
                
                if self.repository:
                    await self.repository.save_result(scored_item)
                
        except Exception as e:
            self.logger.error(f"Error in orchestration pipeline: {e}")
            raise

    async def close(self) -> None:
        """
        Cleanup resources.
        """
        if hasattr(self.data_provider, 'close'):
            await self.data_provider.close()
        if self.repository and hasattr(self.repository, 'close'):
            await self.repository.close()
        self.logger.info("Orchestrator shut down.")
