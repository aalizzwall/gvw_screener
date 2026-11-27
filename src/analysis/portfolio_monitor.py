"""Watchlist (portfolio) take-profit / stop-loss alerts from trade geometry levels.

Pure function over the store + state manager so it is trivially unit-testable.
"""
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

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


DIVIDEND_WINDOW_DAYS = 7


def _last_buy_date(ex_date: date) -> date:
    """Last day a purchase still earns the dividend: previous business day before ex-date.
    ponytail: weekends skipped only; market holidays not modelled."""
    d = ex_date - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def check_dividend_alerts(store, yahoo, window_days: int = DIVIDEND_WINDOW_DAYS,
                          today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Alert once per upcoming ex-dividend date (within window_days) for every watchlist ticker."""
    alerts: List[Dict[str, Any]] = []
    today = today or date.today()
    horizon = today + timedelta(days=window_days)
    for ticker, entry in store.list().items():
        for event in yahoo.fetch_dividend_events(ticker):
            try:
                ex_date = date.fromisoformat(event["date"])
            except (ValueError, TypeError, KeyError):
                continue
            if not (today < ex_date <= horizon):
                continue
            alerted = entry.get("alerted") or {}
            key = f"dividend_{event['date']}"
            if alerted.get(key):
                continue
            alerted[key] = True
            entry["alerted"] = alerted
            alerts.append({
                "ticker": ticker,
                "chat_id": entry.get("chat_id"),
                "level": "dividend",
                "message": (
                    f"🎁 <b>{ticker}</b> 除息：{event['date']} 除息，配息 {event['amount']:.2f} 元/"
                    f"股；領息最後買進日 {_last_buy_date(ex_date):%Y-%m-%d}"
                ),
            })
    store.save()
    return alerts
