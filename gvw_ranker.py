import os, json, sys, pandas as pd, numpy as np
from datetime import datetime

# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
HEALTH_STATUS_FILE = '/home/pi/.hermes/gvw_screener/health_status.json'

def calculate_momentum_score(prices_df, market_df=None):
    """
    Calculates momentum relative to market (Relative Strength)
    GVW Original: RS vs TAIEX is critical for identifying true leaders
    """
    if len(prices_df) < 252:
        return 0
    
    # 1. Calculate Momentum with safety checks
    def safe_div(n, d):
        return n / d if d > 0 else 0

    current_price = prices_df['close'].iloc[-1]
    ret_3m = safe_div(current_price, prices_df['close'].iloc[-63]) - 1 if len(prices_df) >= 63 else 0
    ret_6m = safe_div(current_price, prices_df['close'].iloc[-126]) - 1 if len(prices_df) >= 126 else 0
    ret_12m = safe_div(current_price, prices_df['close'].iloc[-252]) - 1 if len(prices_df) >= 252 else 0
    
    # Original GVW: Focus on 12-month momentum as primary metric
    abs_momentum = (ret_3m + ret_6m + ret_12m) / 3
    
    if market_df is not None and not market_df.empty:
        market_close = market_df['close']
        market_ret_12m = (market_close.iloc[-1] / market_close.iloc[-252]) - 1 if len(market_close) >= 252 else 0
        relative_strength = ret_12m - market_ret_12m
        # Boost momentum if stock outperforms market
        return abs_momentum * (1 + max(0, relative_strength))
    
    return abs_momentum

def calculate_trend_score(prices_df):
    """
    Calculate Trend Score based on GVW / Stage Analysis logic.
    Criteria:
    - Stage 2: Price > MA150 > MA200, MA200 slope up, price near 52-week high.
    """
    if len(prices_df) < 252:
        return 0, False
    
    close = prices_df['close']
    current_price = close.iloc[-1]
    
    sma150 = close.rolling(window=150).mean().iloc[-1]
    sma200 = close.rolling(window=200).mean().iloc[-1]
    sma200_series = close.rolling(window=200).mean()
    sma200_prev = sma200_series.iloc[-20] if len(sma200_series) >= 20 else sma200
    low_52w = close.tail(252).min()
    high_52w = close.tail(252).max()
    
    # Stage 1 Check: Price above MA200 (fundamental trend)
    if current_price <= sma200:
        return 0, False
    
    score = 0.0
    if current_price > sma150:
        score += 0.3
    if sma150 > sma200:
        score += 0.3
    if sma200 > sma200_prev:
        score += 0.2
    
    # Safety check for division by zero
    price_low_ratio = current_price / low_52w if low_52w > 0 else 0
    price_high_ratio = current_price / high_52w if high_52w > 0 else 0
    
    if price_low_ratio > 1.25: 
        score += 0.2
    
    # Strict Stage 2 Check (Minervini Template)
    is_strict_trend = (
        current_price > sma150 and 
        sma150 > sma200 and 
        sma200 > sma200_prev and 
        price_low_ratio > 1.25 and 
        price_high_ratio < 1.30
    )
    
    return score, is_strict_trend

def calculate_entry_signal(prices_df):
    """
    Determine entry timing based on proximity to moving averages.
    Optimized Thresholds:
    - Pullback Buy Zone: -5% to +12% (Allowing more room for strong leaders)
    - Overheated: > 15% (Avoid buying climax runs)
    - Weakening: < -5% (Below support)
    """
    if len(prices_df) < 50:
        return "數據不足"
    
    current_price = prices_df['close'].iloc[-1]
    sma20 = prices_df['close'].rolling(window=20).mean().iloc[-1]
    sma50 = prices_df['close'].rolling(window=50).mean().iloc[-1]
    
    dist20 = (current_price / sma20) - 1 if sma20 > 0 else None
    dist50 = (current_price / sma50) - 1 if sma50 > 0 else None
    
    # Use the tighter of the two MAs for the buy zone to ensure support
    min_dist = min(dist20 if dist20 is not None else 999, dist50 if dist50 is not None else 999)
    max_dist = max(dist20 if dist20 is not None else -999, dist50 if dist50 is not None else -999)
    
    if min_dist >= -0.05 and min_dist <= 0.12:
        return "🟢 回調買入區"
    elif max_dist > 0.15:
        return "🟡 過熱-待回調"
    elif min_dist < -0.05:
        return "🔴 趨勢偏弱"
    else:
        return "⚪ 中立/觀察"

def calculate_quality_score(financials_data):
    """Calculate Growth & Quality: ROE and Revenue/Net Income YoY Growth."""
    try:
        df = pd.DataFrame(financials_data)
        if df.empty: 
            return 0
        
        net_income_df = df[df['type'] == 'IncomeAfterTaxes'].sort_values('date', ascending=False)
        equity_df = df[df['type'] == 'EquityAttributableToOwnersOfParent'].sort_values('date', ascending=False)
        
        if net_income_df.empty or equity_df.empty: 
            return 0
        
        current_ni = net_income_df.iloc[0]['value']
        current_equity = equity_df.iloc[0]['value']
        
        roe = 0
        if current_ni > 0 and current_equity > 0:
            roe = float(current_ni / current_equity)
        
        roe_score = min(1.0, roe / 0.15) 
        
        growth_score = 1.0
        for item_type in ['IncomeAfterTaxes', 'Revenue']:
            type_df = df[df['type'] == item_type].sort_values('date', ascending=False)
            if len(type_df) < 2:
                continue
            
            curr_val = type_df.iloc[0]['value']
            curr_date = pd.to_datetime(type_df.iloc[0]['date'])
            year_ago_date = curr_date - pd.DateOffset(years=1)
            
            type_df['date_dt'] = pd.to_datetime(type_df['date'])
            diff = (type_df['date_dt'] - year_ago_date).abs()
            prev_val = type_df.loc[diff.idxmin()]['value']
            
            if prev_val > 0:
                growth = (curr_val - prev_val) / prev_val
                if growth < 0.2:
                    if growth > -0.1: 
                        growth_score *= max(0.3, 0.5 + 0.2 * growth)
                    else: 
                        growth_score *= 0.1
                else:
                    growth_score *= 1.0
            else:
                growth_score *= 0.5
                
        return roe_score * growth_score
    except Exception:
        return 0

def main():
    market_index_file = '/home/pi/.hermes/gvw_screener/market_index.json'
    market_data = {}
    if os.path.exists(market_index_file):
        with open(market_index_file, 'r') as f:
            market_data = json.load(f)
    
    health_status = {}
    if os.path.exists(HEALTH_STATUS_FILE):
        with open(HEALTH_STATUS_FILE, 'r') as f:
            health_status = json.load(f)

    df_market = pd.DataFrame(market_data.get('prices', []))
    if not df_market.empty:
        df_market['close'] = pd.to_numeric(df_market['close'], errors='coerce')
        df_market = df_market.dropna(subset=['close'])
    
    total_universe_size = 0
    universe_file = '/home/pi/.hermes/gvw_screener/universe.json'
    if os.path.exists(universe_file):
        with open(universe_file, 'r') as f:
            universe_data = json.load(f)
            total_universe_size = len(universe_data) if isinstance(universe_data, dict) else len(universe_data)
    
    files = [f for f in os.listdir(DATA_DIR) if f.endswith('.json')]
    collected_size = len(files)
    
    if total_universe_size > 0:
        progress_pct = (collected_size / total_universe_size) * 100
        print(f"📊 數據採集進度：{collected_size} / {total_universe_size} ({progress_pct:.2f}%)")
    else:
        print(f"📊 目前已採集 {collected_size} 筆資料")
    
    if collected_size == 0:
        print("No data files found.")
        return
    
    print(f"Starting Rank Analysis for {collected_size} stocks...")
    
    all_stocks = []
    for idx, file in enumerate(files, 1):
        try:
            with open(os.path.join(DATA_DIR, file), 'r') as f:
                data = json.load(f)
            
            ticker = data['ticker']
            name = data.get('name', 'Unknown')
            
            df_prices = pd.DataFrame(data['prices'])
            if not df_prices.empty:
                df_prices['close'] = pd.to_numeric(df_prices['close'], errors='coerce')
                df_prices = df_prices.dropna(subset=['close'])
                
                m_val = calculate_momentum_score(df_prices, df_market)
                t_val, is_strict_trend = calculate_trend_score(df_prices)
                q_val = calculate_quality_score(data['financials'])
                entry_signal = calculate_entry_signal(df_prices)
                is_gvw_strict = is_strict_trend and q_val > 0.3

                # Capture sync time ONLY for active stocks to avoid dead-stock noise
                status_info = health_status.get(ticker, {})
                is_dead = status_info.get('status') == 'dead'
                sync_date = data.get('updated_at', '1900-01-01T00:00:00') if not is_dead else None


                all_stocks.append({
                    'ticker': ticker,
                    'name': name,
                    'm_val': m_val,
                    't_val': t_val,
                    'q_val': q_val,
                    'entry_signal': entry_signal,
                    'is_gvw_strict': is_gvw_strict,
                    'sync_date': sync_date
                })
            
            if idx % 100 == 0 or idx == collected_size:
                print(f"Analysis Progress: {idx}/{collected_size} stocks processed...")
        except Exception as e:
            print(f"Error processing file {file}: {e}")
    
    if not all_stocks:
        print("No valid stock data for ranking.")
        return
    
    df_results = pd.DataFrame(all_stocks)
    df_results['m_rank'] = df_results['m_val'].rank(pct=True)
    df_results['q_rank'] = df_results['q_val'].rank(pct=True)
    df_results['t_rank'] = df_results['t_val'] 
    
    # GVW Final Score: Momentum (40%) + Trend (30%) + Quality (30%)
    df_results['final_score'] = (df_results['m_rank'] * 0.4) + (df_results['t_rank'] * 0.3) + (df_results['q_rank'] * 0.3)
    # Add global oldest sync timestamp (the minimum updated_at across all stocks)
    if 'sync_date' in df_results.columns and not df_results['sync_date'].isna().all():
        global_oldest_date = str(df_results['sync_date'].min())
    else:
        global_oldest_date = None
    
    full_results = df_results[['ticker', 'name', 'final_score', 'entry_signal', 'm_rank', 't_rank', 'q_rank']].to_dict('records')
    
    # Write results with the global oldest date
    result_output = {
        'data': full_results,
        'earliest_data_date': global_oldest_date
    }
    with open(RESULT_FILE, 'w') as f:
        json.dump(result_output, f)
    
    print(f"System earliest data collected from: {global_oldest_date or 'N/A'}")
    
    print(f"\n✅ Rank analysis complete. Saved {len(full_results)} stocks to {RESULT_FILE}")
    top_10 = df_results.sort_values('final_score', ascending=False).head(10)
    for i, row in enumerate(top_10.itertuples(), 1):
        gv_tag = " [GVW Strict]" if row.is_gvw_strict else ""
        print(f"{i}. {row.name}{gv_tag} ({row.ticker}) - Score: {row.final_score:.2f} | 狀態：{row.entry_signal}")

if __name__ == "__main__":
    main()
