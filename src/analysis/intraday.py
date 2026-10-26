import os
import json
import logging
from datetime import datetime
from typing import List, Optional
import pandas as pd

from src.state.interfaces import IStateRepository
from src.state.state_manager import StateManager
from src.providers.factory import DataProviderFactory
from src.providers.yahoo_adapter import TickerNotFoundError
from src.config import SIGNAL_CACHE_FILE

logger = logging.getLogger(__name__)

def check_kline_status_from_df(df: pd.DataFrame) -> str:
    """
    Evaluates intraday bar status:
    - BREAKOUT
    - ACCUMULATING
    - NO_SIGNAL
    """
    if df is None or len(df) < 21:
        return "NO_SIGNAL"

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    vol_col = 'volume' if 'volume' in df.columns else ('Trading_Volume' if 'Trading_Volume' in df.columns else 'Volume')
    close = df['close'] if 'close' in df.columns else df['Close']
    vol = df[vol_col]

    avg_vol_20 = vol.tail(20).mean()
    avg_vol_5 = vol.tail(5).mean()
    vol_dry_up = avg_vol_5 <= (0.8 * avg_vol_20) if avg_vol_20 > 0 else False

    last_3_close = close.tail(3)
    curr_close = close.iloc[-1]
    price_range = (last_3_close.max() - last_3_close.min()) / curr_close if curr_close > 0 else 1.0
    bottoming_out = price_range <= 0.05

    prev_5_max = close.iloc[-6:-1].max() if len(close) >= 6 else close.iloc[-1]
    current_vol = vol.iloc[-1]
    breakout = (curr_close > prev_5_max * 1.005) and (current_vol > 1.3 * avg_vol_20)

    if vol_dry_up and bottoming_out and breakout:
        return "BREAKOUT"
    elif vol_dry_up and bottoming_out:
        return "ACCUMULATING"
    else:
        return "NO_SIGNAL"

def format_alert(ticker: str, name: str, score: float, status: str) -> Optional[str]:
    time_str = datetime.now().strftime('%H:%M:%S')
    if status == "BREAKOUT":
        return f"🚀 {name} ({ticker}) 突破!\n得分：{score:.2f}\n狀態：分時三步法全通過\n⏰ {time_str}"
    elif status == "ACCUMULATING":
        return f"⚠️ {name} ({ticker}) 蓄勢中\n得分：{score:.2f}\n狀態：量縮企穩，等待突破\n⏰ {time_str}"
    return None

def run_intraday_monitoring(
    state_manager: Optional[IStateRepository] = None,
    provider_factory: Optional[DataProviderFactory] = None
) -> List[str]:
    """
    Scans Top-30 GVW stocks for real-time breakout signals via 分時三步法.
    """
    state = state_manager or StateManager()
    factory = provider_factory or DataProviderFactory()
    full_report = state.get_ranks()

    all_data = full_report.get('data', [])
    candidates = sorted(
        all_data,
        key=lambda x: x.get('final_score', 0),
        reverse=True,
    )[:30]

    logger.info(f"📋 [START] Intraday Monitoring {len(candidates)} TOP-30 GVW stocks...")

    # Load previous signal cache
    last_signals = {}
    if os.path.exists(SIGNAL_CACHE_FILE):
        try:
            with open(SIGNAL_CACHE_FILE, 'r', encoding='utf-8') as f:
                last_signals = json.load(f)
        except Exception:
            # Ignore corrupted or unreadable signal cache
            pass

    current_signals = {}
    alerts_to_send = []

    for idx, row in enumerate(candidates):
        ticker = row['ticker']
        name = row['name']
        score = float(row.get('final_score', 0))

        # 1. Try real price history from providers
        try:
            prices = factory.fetch_price_history(ticker, days=30)
        except TickerNotFoundError:
            prices = []
        if prices and len(prices) >= 21:
            df = pd.DataFrame(prices)
        else:
            # Fallback to local stored prices
            local_data = state.get_stock_data(ticker)
            local_prices = local_data.get('prices', [])
            df = pd.DataFrame(local_prices) if local_prices else pd.DataFrame()

        status = check_kline_status_from_df(df)
        current_signals[ticker] = status

        if status != "NO_SIGNAL":
            old_status = last_signals.get(ticker, "NO_SIGNAL")
            if old_status != status:
                alert_msg = format_alert(ticker, name, score, status)
                if alert_msg:
                    alerts_to_send.append(alert_msg)

    # Update rank report with kline_status
    status_map = {t: s for t, s in current_signals.items()}
    for row in all_data:
        row['kline_status'] = status_map.get(row['ticker'], "NO_SIGNAL")

    full_report['data'] = all_data
    state.save_ranks(full_report)

    # Save signal cache
    try:
        with open(SIGNAL_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(current_signals, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving {SIGNAL_CACHE_FILE}: {e}")

    return alerts_to_send
