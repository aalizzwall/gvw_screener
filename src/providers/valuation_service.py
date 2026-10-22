import logging
import math
import random
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

TWSE_VALUATION_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d"
TPEX_VALUATION_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    s = str(val).replace(",", "").strip()
    if not s or s in ("-", "--", "N/A", "nan", "null"):
        return 0.0
    try:
        f = float(s)
        return f if math.isfinite(f) else 0.0
    except (ValueError, TypeError):
        return 0.0


class ValuationService:
    """
    Unified valuation service for both TWSE (上市) and TPEx (上櫃) stocks.
    Fetches official daily PE, PB, and Dividend Yield directly from exchange OpenAPIs.
    Caches responses in-memory for 1 hour to prevent redundant HTTP requests.
    """

    def __init__(self, cache_ttl_seconds: int = 3600):
        self.cache_ttl = cache_ttl_seconds
        self._twse_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._twse_cache_time: float = 0.0
        self._tpex_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._tpex_cache_time: float = 0.0

    def _get_headers(self) -> Dict[str, str]:
        return {"User-Agent": random.choice(USER_AGENTS)}

    def _fetch_twse(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        if self._twse_cache is not None and (now - self._twse_cache_time < self.cache_ttl):
            return self._twse_cache

        try:
            resp = requests.get(TWSE_VALUATION_URL, headers=self._get_headers(), timeout=15)
            if resp.status_code == 200:
                raw_list = resp.json()
                if isinstance(raw_list, list):
                    table: Dict[str, Dict[str, Any]] = {}
                    for row in raw_list:
                        if isinstance(row, dict):
                            code = str(row.get("Code", "")).strip()
                            if code:
                                table[code] = row
                    self._twse_cache = table
                    self._twse_cache_time = now
                    logger.info("Successfully fetched %d TWSE valuation records.", len(table))
                    return table
            logger.warning("TWSE valuation fetch returned status %s", resp.status_code)
        except Exception as exc:
            logger.warning("Failed to fetch TWSE valuation data: %s", exc)

        return self._twse_cache or {}

    def _fetch_tpex(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        if self._tpex_cache is not None and (now - self._tpex_cache_time < self.cache_ttl):
            return self._tpex_cache

        try:
            resp = requests.get(TPEX_VALUATION_URL, headers=self._get_headers(), timeout=15)
            if resp.status_code == 200:
                raw_list = resp.json()
                if isinstance(raw_list, list):
                    table: Dict[str, Dict[str, Any]] = {}
                    for row in raw_list:
                        if isinstance(row, dict):
                            code = str(row.get("SecuritiesCompanyCode", "")).strip()
                            if code:
                                table[code] = row
                    self._tpex_cache = table
                    self._tpex_cache_time = now
                    logger.info("Successfully fetched %d TPEx valuation records.", len(table))
                    return table
            logger.warning("TPEx valuation fetch returned status %s", resp.status_code)
        except Exception as exc:
            logger.warning("Failed to fetch TPEx valuation data: %s", exc)

        return self._tpex_cache or {}

    def get_valuation(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get PE, PB, and Dividend Yield for a given stock ticker.
        Returns a dictionary or None if not found in either exchange.
        """
        ticker_str = str(ticker).strip()

        # 1. Try TWSE (Listed 上市)
        twse_data = self._fetch_twse()
        if ticker_str in twse_data:
            row = twse_data[ticker_str]
            pe_val = _safe_float(row.get("PEratio"))
            pb_val = _safe_float(row.get("PBratio"))
            yield_val = _safe_float(row.get("DividendYield"))
            is_loss = (pb_val > 0 and pe_val <= 0)
            return {
                "ticker": ticker_str,
                "name": str(row.get("Name", "")).strip(),
                "pe": pe_val,
                "pb": pb_val,
                "yield": yield_val,
                "is_loss": is_loss,
                "source": "twse",
                "date": str(row.get("Date", "")),
            }

        # 2. Try TPEx (OTC 上櫃)
        tpex_data = self._fetch_tpex()
        if ticker_str in tpex_data:
            row = tpex_data[ticker_str]
            pe_val = _safe_float(row.get("PriceEarningRatio"))
            pb_val = _safe_float(row.get("PriceBookRatio"))
            yield_val = _safe_float(row.get("YieldRatio"))
            is_loss = (pb_val > 0 and pe_val <= 0)
            return {
                "ticker": ticker_str,
                "name": str(row.get("CompanyName", "")).strip(),
                "pe": pe_val,
                "pb": pb_val,
                "yield": yield_val,
                "is_loss": is_loss,
                "source": "tpex",
                "date": str(row.get("Date", "")),
            }

        return None


_default_valuation_service: Optional[ValuationService] = None


def get_valuation_service() -> ValuationService:
    global _default_valuation_service
    if _default_valuation_service is None:
        _default_valuation_service = ValuationService()
    return _default_valuation_service
