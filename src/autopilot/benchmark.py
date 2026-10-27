"""TAIEX benchmark series used to turn raw pick returns into excess returns."""
import json
import logging
import os
from typing import Any, Dict, Optional

from src.config import DATA_DIR

logger = logging.getLogger("gvw.autopilot.benchmark")

TAIEX_NAME = "發行量加權股價指數"
BACKFILL_BELOW = 30  # try a one-off history backfill while we hold fewer dates than this


def _roc_to_iso(roc: str) -> Optional[str]:
    """'1151022' -> '2026-10-22'."""
    roc = str(roc).strip()
    if len(roc) < 7 or not roc.isdigit():
        return None
    return f"{int(roc[:-4]) + 1911:04d}-{roc[-4:-2]}-{roc[-2:]}"


class BenchmarkSeries:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(DATA_DIR, "benchmark.json")
        self.closes: Dict[str, float] = self._load()

    def _load(self) -> Dict[str, float]:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    return {str(k): float(v) for k, v in json.load(f).items()}
        except Exception as e:
            logger.warning(f"Benchmark file unreadable, starting fresh: {e}")
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(self.closes.items())[-400:]), f, indent=0)
        os.replace(tmp, self.path)

    def ingest_market_index(self, market_index: Dict[str, Any]) -> bool:
        """Pick today's TAIEX close out of the TWSE index snapshot."""
        for row in (market_index or {}).get("stats", []):
            if row.get("指數") == TAIEX_NAME:
                day = _roc_to_iso(row.get("日期", ""))
                try:
                    close = float(str(row.get("收盤指數", "")).replace(",", ""))
                except ValueError:
                    return False
                if day and close > 0:
                    self.closes[day] = close
                    return True
        return False

    def backfill(self) -> int:
        """Best-effort history from Yahoo (^TWII); silently skipped if unavailable."""
        try:
            import yfinance as yf

            hist = yf.Ticker("^TWII").history(period="6mo", auto_adjust=False)
            added = 0
            for ts, row in hist.iterrows():
                day = ts.strftime("%Y-%m-%d")
                if day not in self.closes and row["Close"] > 0:
                    self.closes[day] = float(row["Close"])
                    added += 1
            return added
        except Exception as e:
            logger.info(f"Benchmark backfill skipped: {e}")
            return 0

    def update(self, market_index: Dict[str, Any]) -> None:
        changed = self.ingest_market_index(market_index)
        if len(self.closes) < BACKFILL_BELOW:
            changed = self.backfill() > 0 or changed
        if changed:
            self._save()

    def ret(self, start: str, end: str) -> Optional[float]:
        a, b = self.closes.get(start), self.closes.get(end)
        if a and b:
            return b / a - 1
        return None
