"""Watchlist (portfolio) take-profit / stop-loss alerts from trade geometry levels.

Pure function over the store + state manager so it is trivially unit-testable.
"""
import logging
from typing import Any, Dict, List

from src.analysis.trade_geometry import compute_trade_plan

logger = logging.getLogger(__name__)

LEVEL_LABELS = {"target_1": "第一目標", "target_2": "第二目標", "stop_loss": "止損"}


def check_portfolio(store, state_manager) -> List[Dict[str, Any]]:
    """Return alert dicts {ticker, chat_id, level, message}; each level alerts only once."""
    alerts: List[Dict[str, Any]] = []
    for ticker, entry in store.list().items():
        stock_data = state_manager.get_stock_data(ticker) or {}
        bars = stock_data.get("prices", [])
        if not bars:
            continue
        plan = compute_trade_plan(bars)
        if not plan:
            continue
        try:
            current = float(bars[-1].get("close"))
        except (TypeError, ValueError):
            continue
        alerted = entry.get("alerted") or {}
        for level in ("target_1", "target_2", "stop_loss"):
            level_price = plan.get(level)
            if level_price is None or alerted.get(level):
                continue
            hit = current <= level_price if level == "stop_loss" else current >= level_price
            if hit:
                alerted[level] = True
                entry["alerted"] = alerted
                label = LEVEL_LABELS[level]
                alerts.append({
                    "ticker": ticker,
                    "chat_id": entry.get("chat_id"),
                    "level": level,
                    "message": (
                        f"⭐ <b>{ticker}</b> 已達到{label}：目前 {current:.2f} 元"
                        f"（{label} {level_price:.2f} 元，進場價 {entry['entry_price']:.2f} 元）"
                    ),
                })
        store.save()
    return alerts
