import logging
from typing import Dict, Any, List, Optional
import pandas as pd
from src.state.interfaces import IStateRepository
from src.state.state_manager import StateManager

logger = logging.getLogger(__name__)

def check_kline_three_steps(ticker: str, state_manager: Optional[IStateRepository] = None) -> Optional[Dict[str, Any]]:
    """
    Mathematically verify the K-line 3-step method:
    1. Volume Dry-up: Recent 5d avg vol <= 70% of 20d avg vol
    2. Bottoming Out: Price range of last 3d <= 3% of current price
    3. Breakout: Current close > max of previous 5d AND current vol > 1.5x 20d avg vol
    """
    state = state_manager or StateManager()
    data = state.get_stock_data(ticker)
    if not data or 'prices' not in data:
        return None

    try:
        df = pd.DataFrame(data['prices'])
        if df.empty or 'close' not in df.columns:
            return None

        # Normalize column names
        vol_col = 'volume' if 'volume' in df.columns else ('Trading_Volume' if 'Trading_Volume' in df.columns else 'vol')
        if vol_col not in df.columns:
            return None

        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['vol'] = pd.to_numeric(df[vol_col], errors='coerce')
        df = df.dropna(subset=['close', 'vol'])

        if len(df) < 21:
            return None

        # 1. Volume Dry-up (量能萎縮)
        avg_vol_20 = df['vol'].tail(20).mean()
        avg_vol_5 = df['vol'].tail(5).mean()
        vol_dry_up = avg_vol_5 <= (0.7 * avg_vol_20) if avg_vol_20 > 0 else False

        # 2. Bottoming Out (支撐企穩)
        last_3_days = df['close'].tail(3)
        current_close = df['close'].iloc[-1]
        price_range = (last_3_days.max() - last_3_days.min()) / current_close if current_close > 0 else 1.0
        bottoming_out = price_range <= 0.03

        # 3. Breakout (帶量突破)
        prev_5_max = df['close'].iloc[-6:-1].max() if len(df) >= 6 else df['close'].iloc[-1]
        current_vol = df['vol'].iloc[-1]

        breakout_price = current_close > prev_5_max
        breakout_vol = current_vol > (1.5 * avg_vol_20) if avg_vol_20 > 0 else False
        breakout = breakout_price and breakout_vol

        return {
            "vol_dry_up": bool(vol_dry_up),
            "bottoming_out": bool(bottoming_out),
            "breakout": bool(breakout),
            "all_passed": bool(vol_dry_up and bottoming_out and breakout)
        }
    except Exception as e:
        logger.warning(f"Error checking K-line for {ticker}: {e}")
        return None

def get_strong_buy_signals(state_manager: Optional[IStateRepository] = None) -> List[Dict[str, Any]]:
    """
    Scan high-score candidates (Score >= 0.95 in pullback zone) for K-line 3-step verification.
    """
    state = state_manager or StateManager()
    ranks_data = state.get_ranks()
    all_data = ranks_data.get('data', [])

    candidates = [
        row for row in all_data
        if row.get('final_score', 0) >= 0.95 and row.get('entry_signal') == '🟢 回調買入區'
    ]
    candidates_sorted = sorted(candidates, key=lambda x: x.get('final_score', 0), reverse=True)

    signals = []
    for row in candidates_sorted:
        ticker = row['ticker']
        res = check_kline_three_steps(ticker, state_manager=state)
        if res and res.get('all_passed'):
            signals.append({
                'name': row.get('name', ticker),
                'ticker': ticker,
                'score': row.get('final_score', 0)
            })

    return signals

