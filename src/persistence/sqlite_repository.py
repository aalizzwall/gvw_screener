import aiosqlite
import json
import logging
from typing import Any, Dict, Optional
from src.interfaces.repository import DataRepository

logger = logging.getLogger(__name__)

class SQLiteRepository(DataRepository):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        try:
            self._conn = await aiosqlite.connect(self.db_path)
            await self._create_tables()
        except Exception as e:
            logger.error(f"Failed to connect to SQLite database at {self.db_path}: {e}")
            raise

    async def _create_tables(self) -> None:
        try:
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS scoring_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    score REAL NOT NULL,
                    timestamp TEXT,
                    metadata_json TEXT
                )
            """)
            await self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to create tables: {e}")
            raise

    async def save_result(self, result: Dict[str, Any]) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        try:
            ticker = result.get("ticker")
            score = result.get("score")
            timestamp = result.get("timestamp")
            metadata = result.get("metadata", {})
            
            if ticker is None or score is None:
                raise ValueError("Result must contain 'ticker' and 'score'")

            await self._conn.execute(
                """
                INSERT INTO scoring_results (ticker, score, timestamp, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (ticker, score, timestamp, json.dumps(metadata))
            )
            await self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to save result to SQLite: {e}")
            raise

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
