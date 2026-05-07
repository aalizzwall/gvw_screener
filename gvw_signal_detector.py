import os, json, pandas as pd, numpy as np

# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'

def check_kline_three_steps(ticker):
    """
    Mathematically verify the K-line 3-step method:
    1. Volume Dry-up: Recent 5d avg vol <= 70% of 20d avg vol
    2. Bottoming Out: Price range of last 3d <= 3% of current price
    3. Breakout: Current close > max of previous 5d AND current vol > 1.5x 20d avg vol
    """
    file_path = os.path.join(DATA_DIR, f"{ticker}.json")
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        df = pd.DataFrame(data['prices'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['vol'] = pd.to_numeric(df['Trading_Volume'], errors='coerce')
        df = df.dropna(subset=['close', 'vol'])

        if len(df) < 21:
            return None

        # 1. Volume Dry-up (量能萎縮)
        # Average volume of last 5 days vs 20 days
        avg_vol_20 = df['vol'].tail(20).mean()
        avg_vol_5 = df['vol'].tail(5).mean()
        vol_dry_up = avg_vol_5 <= (0.7 * avg_vol_20)

        # 2. Bottoming Out (支撐企穩)
        # Price range of last 3 days <= 3% of current price
        last_3_days = df['close'].tail(3)
        price_range = (last_3_days.max() - last_3_days.min()) / df['close'].iloc[-1]
        bottoming_out = price_range <= 0.03

        # 3. Breakout (帶量突破)
        # Current close > max of previous 5 days (excluding today)
        prev_5_max = df['close'].iloc[-6:-1].max()
        current_close = df['close'].iloc[-1]
        current_vol = df['vol'].iloc[-1]
        
        breakout_price = current_close > prev_5_max
        breakout_vol = current_vol > (1.5 * avg_vol_20)
        breakout = breakout_price and breakout_vol

        # Return detailed status for the report
        return {
            "vol_dry_up": vol_dry_up,
            "bottoming_out": bottoming_out,
            "breakout": breakout,
            "all_passed": vol_dry_up and bottoming_out and breakout
        }
    except Exception as e:
        print(f"Error checking kline for {ticker}: {e}")
        return None

def get_strong_buy_signals():
    """
    Scan all high-score stocks (Score >= 0.95) for K-line 3-step verification.
    This captures both pullback entries and high-momentum breakouts.
    """
    if not os.path.exists(RESULT_FILE):
        return []
    
    with open(RESULT_FILE, 'r') as f:
        result = json.load(f)
    
    all_data = result.get('data', []) if isinstance(result, dict) else result
    
    # Filter for high-score candidates currently in the pullback buy zone
    candidates = [row for row in all_data if row['final_score'] >= 0.95 and row['entry_signal'] == '🟢 回調買入區']
    candidates_sorted = sorted(candidates, key=lambda x: x['final_score'], reverse=True)
    
    signals = []
    for row in candidates_sorted:
        ticker = row['ticker']
        res = check_kline_three_steps(ticker)
        if res and res['all_passed']:
            signals.append({
                'name': row['name'],
                'ticker': ticker,
                'score': row['final_score']
            })
    
    return signals
    return signals

if __name__ == "__main__":
    # Test run
    signals = get_strong_buy_signals()
    print(f"Found {len(signals)} strong buy signals: {signals}")
