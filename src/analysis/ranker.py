import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np

from src.config import DATA_DIR
from src.state.interfaces import IStateRepository
from src.state.state_manager import StateManager

logger = logging.getLogger(__name__)

DEFAULT_SCORE_WEIGHTS: Dict[str, float] = {"momentum": 0.4, "trend": 0.3, "quality": 0.3}


def load_score_weights(path: Optional[str] = None) -> Dict[str, float]:
    """Loads owner-approved score weights (data/tuning_weights.json), falling back to defaults."""
    target = path or os.path.join(DATA_DIR, "tuning_weights.json")
    try:
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as f:
                raw = json.load(f)
            weights = {k: float(raw[k]) for k in DEFAULT_SCORE_WEIGHTS}
            total = sum(weights.values())
            if total > 0 and all(v >= 0 for v in weights.values()):
                return {k: v / total for k, v in weights.items()}
    except Exception as e:
        logger.warning(f"Invalid tuning weights file, using defaults: {e}")
    return dict(DEFAULT_SCORE_WEIGHTS)

def calculate_momentum_score(current_price: float, prices_close: np.ndarray, market_close: Optional[np.ndarray] = None) -> float:
    """
    Optimized momentum calculation using numpy.
    GVW Original: RS vs TAIEX is critical for identifying true leaders.
    """
    if len(prices_close) < 252:
        return 0.0

    windows = {'3m': 63, '6m': 126, '12m': 252}
    returns = {}

    for label, window in windows.items():
        window_size = np.min([window, len(prices_close)])
        window_prices = prices_close[-window_size:]
        returns[label] = (current_price / window_prices[0]) - 1 if window_prices[0] > 0 else 0.0

    # Absolute Momentum (average of 3 windows)
    abs_momentum = sum(returns.values()) / 3.0

    # Relative Strength (RS compared to Market Index)
    if market_close is not None and len(market_close) >= 252:
        m_returns = {}
        for label, window in windows.items():
            window_size = np.min([window, len(market_close)])
            window_m = market_close[-window_size:]
            m_returns[label] = (market_close[-1] / window_m[0]) - 1 if window_m[0] > 0 else 0.0

        rs_value = (
            (returns['3m'] - m_returns['3m']) * 0.2 +
            (returns['6m'] - m_returns['6m']) * 0.3 +
            (returns['12m'] - m_returns['12m']) * 0.5
        )
        return abs_momentum * (1.0 + rs_value)

    return abs_momentum

def calculate_trend_score(prices_df: pd.DataFrame) -> Tuple[float, bool]:
    """
    Calculate Trend Score based on GVW / Stage 2 Analysis logic.
    - Stage 2: Price > MA150 > MA200, MA200 slope up, price near 52-week high.
    """
    if len(prices_df) < 252:
        return 0.0, False

    close = prices_df['close']
    current_price = close.iloc[-1]

    sma150 = close.rolling(window=150).mean().iloc[-1]
    sma200 = close.rolling(window=200).mean().iloc[-1]
    sma200_series = close.rolling(window=200).mean()
    sma200_prev = sma200_series.iloc[-20] if len(sma200_series) >= 20 else sma200
    low_52w = close.tail(252).min()
    high_52w = close.tail(252).max()

    if current_price <= sma200:
        return 0.0, False

    score = 0.0
    if current_price > sma150:
        score += 0.3
    if sma150 > sma200:
        score += 0.3
    if sma200 > sma200_prev:
        score += 0.2

    price_low_ratio = current_price / low_52w if low_52w > 0 else 0
    price_high_ratio = current_price / high_52w if high_52w > 0 else 0

    if price_low_ratio > 1.25:
        score += 0.2

    is_strict_trend = (
        current_price > sma150 and
        sma150 > sma200 and
        sma200 > sma200_prev and
        price_low_ratio > 1.25 and
        price_high_ratio < 1.30
    )

    return score, is_strict_trend

def calculate_entry_signal(prices_df: pd.DataFrame) -> str:
    """
    Determine entry timing based on proximity to MA20 and MA50.
    """
    if len(prices_df) < 50:
        return "數據不足"

    current_price = prices_df['close'].iloc[-1]
    sma20 = prices_df['close'].rolling(window=20).mean().iloc[-1]
    sma50 = prices_df['close'].rolling(window=50).mean().iloc[-1]

    dist20 = (current_price / sma20) - 1 if sma20 > 0 else None
    dist50 = (current_price / sma50) - 1 if sma50 > 0 else None

    min_dist = min(dist20 if dist20 is not None else 999, dist50 if dist50 is not None else 999)
    max_dist = max(dist20 if dist20 is not None else -999, dist50 if dist50 is not None else -999)

    if -0.05 <= min_dist <= 0.12:
        return "🟢 回調買入區"
    elif max_dist > 0.15:
        return "🟡 過熱-待回調"
    elif min_dist < -0.05:
        return "🔴 趨勢偏弱"
    else:
        return "⚪ 中立/觀察"

def calculate_quality_score(financials_data: Any) -> float:
    """Calculate Growth & Quality: ROE and revenue growth."""
    try:
        if isinstance(financials_data, dict):
            # Formatted from DataProvider
            pl = financials_data.get('income_statement', {})
            bs = financials_data.get('balance_sheet', {})
            net_income = float(pl.get('net_income', 0))
            equity = float(bs.get('total_equity', 0))
            roe = (net_income / equity) if equity > 0 else 0.0
            return min(1.0, max(0.0, roe / 0.15))
        elif isinstance(financials_data, list):
            # Raw list format fallback
            df = pd.DataFrame(financials_data)
            if df.empty:
                return 0.0
            net_income_df = df[df['type'] == 'IncomeAfterTaxes'].sort_values('date', ascending=False)
            equity_df = df[df['type'] == 'EquityAttributableToOwnersOfParent'].sort_values('date', ascending=False)
            if net_income_df.empty or equity_df.empty:
                return 0.0
            current_ni = net_income_df.iloc[0]['value']
            current_equity = equity_df.iloc[0]['value']
            roe = (current_ni / current_equity) if current_equity > 0 else 0.0
            return min(1.0, max(0.0, float(roe / 0.15)))
    except Exception:
        # Fall back to quality score 0.0 if financial statement parsing fails
        pass
    return 0.0

def run_ranking(state_manager: Optional[IStateRepository] = None) -> Dict[str, Any]:
    """
    Executes full screener rank analysis across all collected stocks.
    Saves and returns the ranking report.
    """
    state = state_manager or StateManager()
    market_index = state.get_market_index()
    health_status = state.get_health_status()

    # Load market index history
    df_market = pd.DataFrame(market_index.get('history', []))
    market_close = None
    if not df_market.empty and 'close' in df_market.columns:
        df_market['close'] = pd.to_numeric(df_market['close'], errors='coerce')
        df_market = df_market.dropna(subset=['close'])
        market_close = df_market['close'].values

    stock_files = state.list_stock_tickers()
    if not stock_files:
        logger.warning("No data files found for ranking.")
        return {"data": [], "last_update_time": datetime.now().strftime('%Y-%m-%d')}

    logger.info(f"Starting Rank Analysis for {len(stock_files)} stocks...")
    all_stocks = []

    for idx, ticker in enumerate(stock_files, 1):
        try:
            data = state.get_stock_data(ticker)
            if 'ticker' not in data or 'prices' not in data:
                continue

            name = data.get('name', 'Unknown')
            df_prices = pd.DataFrame(data['prices'])
            if df_prices.empty or 'close' not in df_prices.columns:
                continue

            df_prices['close'] = pd.to_numeric(df_prices['close'], errors='coerce')
            df_prices = df_prices.dropna(subset=['close'])
            if 'date' in df_prices.columns:
                df_prices = df_prices.sort_values('date')
            if len(df_prices) < 20:
                continue

            current_price = df_prices['close'].iloc[-1]
            prices_close = df_prices['close'].values

            m_val = calculate_momentum_score(current_price, prices_close, market_close)
            t_val, is_strict_trend = calculate_trend_score(df_prices)
            q_val = calculate_quality_score(data.get('financials', {}))
            entry_signal = calculate_entry_signal(df_prices)
            is_gvw_strict = is_strict_trend and q_val > 0.3

            is_dead = health_status.get(ticker, {}).get('status') == 'dead'
            sync_date = data.get('updated_at', '1900-01-01T00:00:00') if not is_dead else None
            price_data_as_of = None
            if 'date' in df_prices.columns:
                latest_price_date = pd.to_datetime(df_prices['date'], errors='coerce').max()
                if pd.notna(latest_price_date):
                    price_data_as_of = latest_price_date.strftime('%Y-%m-%d')

            b_info = data.get('basic_info', {})
            pb_val = float(b_info.get('pb', 0) or 0)
            yield_val = float(b_info.get('yield', 0) or 0)
            pe_val = float(b_info.get('pe', 0) or 0)
            if pb_val == 0 and yield_val == 0:
                try:
                    from src.providers.valuation_service import get_valuation_service
                    val = get_valuation_service().get_valuation(ticker)
                    if val:
                        pb_val = val['pb']
                        yield_val = val['yield']
                        pe_val = val['pe']
                except Exception:
                    # Ignore valuation lookup failure and proceed with defaults
                    pass

            all_stocks.append({
                'ticker': ticker,
                'name': name,
                'm_val': m_val,
                't_val': t_val,
                'q_val': q_val,
                'entry_signal': entry_signal,
                'is_gvw_strict': is_gvw_strict,
                'sync_date': sync_date,
                'price_data_as_of': price_data_as_of,
                'price_data_source': data.get(
                    'price_data_source',
                    df_prices.iloc[-1].get('source', 'unknown'),
                ),
                'roe': b_info.get('roe', 0),
                'pb': pb_val,
                'pe': pe_val,
                'yield': yield_val
            })

            if idx % 100 == 0 or idx == len(stock_files):
                logger.info(f"Analysis Progress: {idx}/{len(stock_files)} stocks processed...")
        except Exception as e:
            logger.warning(f"Error processing stock {ticker}: {e}")

    if not all_stocks:
        logger.warning("No valid stock data for ranking.")
        return {"data": [], "last_update_time": datetime.now().strftime('%Y-%m-%d')}

    df_results = pd.DataFrame(all_stocks)
    df_results['m_rank'] = df_results['m_val'].rank(pct=True)
    df_results['q_rank'] = df_results['q_val'].rank(pct=True)
    df_results['t_score'] = df_results['t_val']

    # GVW Final Score: Momentum / Trend / Quality (default 40/30/30, tunable via approved proposal)
    weights = load_score_weights()
    df_results['final_score'] = (
        (df_results['m_rank'] * weights["momentum"]) +
        (df_results['t_score'] * weights["trend"]) +
        (df_results['q_rank'] * weights["quality"])
    )

    valid_dates = df_results['sync_date'].dropna()
    global_oldest_date = str(min(valid_dates)) if not valid_dates.empty else None

    full_results = df_results[[
        'ticker', 'name', 'final_score', 'entry_signal', 'm_rank', 't_score', 'q_rank', 'is_gvw_strict'
    ]].sort_values('final_score', ascending=False).to_dict('records')

    result_output = {
        'data': full_results,
        'earliest_data_date': global_oldest_date,
        'last_update_time': datetime.now().strftime('%Y-%m-%d')
    }

    state.save_ranks(result_output)
    logger.info(f"✅ Rank analysis complete. Saved {len(full_results)} stocks.")
    return result_output
