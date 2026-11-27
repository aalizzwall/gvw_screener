import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

API = "https://mopsov.twse.com.tw/server-java/apiM/ipo/interfaces"
IPO_TYPES = ("初上櫃", "初上市")  # 新股 IPO；增資/可转债不播 (Q10)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://mopsov.twse.com.tw/IPO/imformationhub.html",
}


def _post(endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
    resp = requests.post(
        f"{API}/{endpoint}",
        params={"requestBody": json.dumps(body), "requestHeader": "{}"},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    resp.encoding = "utf-8"  # TWSE endpoint omits charset; json() would default to latin-1
    return resp.json().get("responseBody", {})


def _apply_info(stock_no: str) -> Optional[Dict[str, str]]:
    """公開申購 block for an IPO case; None when there is no 申購 (流標/純競價)."""
    detail = _post("IpoQueryFast", {"stockNo": stock_no, "order": "desc"}).get("allType", [])
    for wrapper in detail:
        block = next((b for b in wrapper.get("allData", []) if b.get("type") == "申購"), None)
        if block:
            return dict(zip(block.get("fields", []), block.get("data", [])))
    return None


def build_ipo_message(code: str, name: str, kind: str, fields: Dict[str, str]) -> str:
    period = fields.get("申購期間（起迄）", "")
    deadline = period[-10:] if len(period) >= 10 else "?"  # "YYYY-MM-DD-YYYY-MM-DD"
    return (
        f"🎯 新股申購：{name}({code}) {kind}\n"
        f"承銷價 {fields.get('承銷價（元）', '?')} 元/張，申購 {period or '?'}"
        f"（截止 {deadline}），承銷 {fields.get('實際承銷張數', '?')} 張，"
        f"抽籤 {fields.get('抽籤日期', '?')}"
    )


def fetch_ipo_alerts(store_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """New IPO cases not yet broadcast; dedup persisted to ipo_alerted.json."""
    path = store_path or os.path.join(DATA_DIR, "ipo_alerted.json")
    alerted: List[str] = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                alerted = [str(x) for x in json.load(f)]
        except Exception as e:
            logger.warning(f"[IPO] failed to load {path}: {e}")
    alerted_set = set(alerted)

    alerts: List[Dict[str, Any]] = []
    try:
        cases = _post("IpoQueryCase", {}).get("allType", [])
    except Exception as e:
        logger.warning(f"[IPO] case list failed: {e}")
        return alerts

    for case in cases:
        data = case.get("data") or []
        if len(data) < 3:
            continue
        code, name, kind = data[0], data[1], data[2]
        if not code or not any(t in kind for t in IPO_TYPES):
            continue
        key = f"ipo_{code}"
        if key in alerted_set:
            continue
        try:
            fields = _apply_info(code)
        except Exception as e:
            logger.warning(f"[IPO] detail fetch failed for {code}: {e}")
            continue
        if not fields:
            logger.info(f"[IPO] {code} has no 申購 block, skip")
            continue
        alerts.append({"ticker": code, "message": build_ipo_message(code, name, kind, fields)})
        alerted.append(key)

    if alerts:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sorted(set(alerted)), f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[IPO] failed to save {path}: {e}")
    return alerts
