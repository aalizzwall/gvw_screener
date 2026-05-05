import os, json, sys, pandas as pd, numpy as np
from datetime import datetime

# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'

def calculate_momentum_score(prices_df, market_df=None):
    """Calculates momentum relative to market (Relative Strength)"""
    if len(prices_df) < 252:
        return 0
    
    current_price = prices_df['close'].iloc[-1]
    ret_3m = (current_price / prices_df['close'].iloc[-63]) - 1 if len(prices_df) >= 63 else 0
    ret_6m = (current_price / prices_df['close'].iloc[-126]) - 1 if len(prices_df) >= 126 else 0
    ret_12m = (current_price / prices_df['close'].iloc[-252]) - 1 if len(prices_df) >= 252 else 0
    abs_momentum = (ret_3m + ret_6m + ret_12m) / 3
    
    if market_df is not None and not market_df.empty:
        market_close = market_df['close']
        market_ret_12m = (market_close.iloc[-1] / market_close.iloc[-252]) - 1 if len(market_close) >= 252 else 0
        # Relative Strength: Stock 12m return vs Market 12m return
        relative_strength = ret_12m - market_ret_12m
        # Combine absolute momentum with relative strength (boost if RS is positive)
        return abs_momentum * (1 + max(0, relative_strength))
    
    return abs_momentum

def calculate_trend_score(prices_df):
    """
    Calculate Trend Score based on GVW / Stage Analysis logic.
    Returns (score, strict_criteria_met)
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
    
    if current_price < sma200:
        return 0, False
    
    # Scoring Logic
    score = 0.0
    if current_price > sma150: score += 0.3
    if sma150 > sma200: score += 0.3
    if sma200 > sma200_prev: score += 0.2
    if current_price > (low_52w * 1.25): score += 0.2
    
    # Strict Stage 2 Check (Boolean) - GVW original criteria
    # Price > SMA150 > SMA200, SMA200 slope up, within reasonable range of 52w high
    is_strict_trend = (
        current_price > sma150 and 
        sma150 > sma200 and 
        sma200 > sma200_prev and 
        current_price > (low_52w * 1.25) and 
        current_price < (high_52w * 1.25)  # Within 25% of 52-week high
    )
    
    return score, is_strict_trend

def calculate_entry_signal(prices_df):
    """
    Determine entry timing based on proximity to 20-day and 50-day SMA.
    Logic: Buy when price pulls back to MA but stays above it.
    GVW Original: Pullback buy when Price > MA20/MA50 AND within reasonable range
    """
    if len(prices_df) < 50:
        return "數據不足"
    
    current_price = prices_df['close'].iloc[-1]
    sma20 = prices_df['close'].rolling(window=20).mean().iloc[-1]
    sma50 = prices_df['close'].rolling(window=50).mean().iloc[-1]
    
    # GVW Logic: Pullback buy when price is near MA but not too far above
    dist20 = (current_price / sma20) - 1 if sma20 > 0 else None
    dist50 = (current_price / sma50) - 1 if sma50 > 0 else None
    
    # Signal Logic: Within 3% above MA20 or MA50 = Pullback Buy Zone
    if dist20 is not None and -0.05 <= dist20 <= 0.03:
        return "🟢 回調買入區"
    elif dist50 is not None and -0.10 <= dist50 <= 0.03:
        return "🟢 回調買入區"
    elif dist20 > 0.05 if dist20 is not None else False:
        return "🟡 過熱-待回調"
    elif dist20 < -0.05 if dist20 is not None else False:
        return "🔴 趨勢偏弱"
    else:
        return "⚪ 中立/觀察"

def calculate_quality_score(financials_data):
    """Calculate Growth & Quality: ROE and Revenue/Net Income YoY Growth"""
    try:
        df = pd.DataFrame(financials_data)
        if df.empty: 
            return 0
        
        # 1. Calculate ROE (Efficiency) - GVW requires ROE > 10%
        net_income_df = df[df['type'] == 'IncomeAfterTaxes'].sort_values('date', ascending=False)
        equity_df = df[df['type'] == 'EquityAttributableToOwnersOfParent'].sort_values('date', ascending=False)
        
        if net_income_df.empty or equity_df.empty: 
            return 0
        
        current_ni = net_income_df.iloc[0]['value']
        current_equity = equity_df.iloc[0]['value']
        
        roe = 0
        if current_ni > 0 and current_equity > 0:
            roe = float(current_ni / current_equity)
        
        # GVW Original Criteria: ROE must be at least 10% (0.1)
        if roe < 0.1:
            return 0
        
        # 2. Calculate YoY Growth (Growth) - GVW requires Revenue & Net Income YoY > 20%
        growth_score = 1.0
        for item_type in ['IncomeAfterTaxes', 'Revenue']:
            type_df = df[df['type'] == item_type].sort_values('date', ascending=False)
            if len(type_df) < 2:
                continue
            
            curr_val = type_df.iloc[0]['value']
            curr_date = pd.to_datetime(type_df.iloc[0]['date'])
            year_ago_date = curr_date - pd.DateOffset(years=1)
            
            # Get value nearest to year_ago_date
            type_df['date_dt'] = pd.to_datetime(type_df['date'])
            diff = (type_df['date_dt'] - year_ago_date).abs()
            prev_val = type_df.loc[diff.idxmin()]['value']
            
            if prev_val > 0:
                growth = (curr_val - prev_val) / prev_val
                
                # GVW Original Criteria: High growth is essential (YoY >= 20%)
                if growth < 0.2:
                    growth_score *= max(0, 0.5 + 0.3 * growth)  # Scale down for low growth
                    if growth <= -0.2:  # Exclude negative growth stocks
                        return 0
                else:
                    growth_score *= 1.0  # Good growth, no penalty
            
            else:
                growth_score *= 0.5
                
        return roe * growth_score
        
    except Exception:
        return 0

def main():
    # --- Market Index Loading ---
    market_index_file = '/home/pi/.hermes/gvw_screener/market_index.json'
    market_data = {}
    if os.path.exists(market_index_file):
        with open(market_index_file, 'r') as f:
            market_data = json.load(f)
    
    df_market = pd.DataFrame(market_data.get('prices', []))
    if not df_market.empty:
        df_market['close'] = pd.to_numeric(df_market['close'], errors='coerce')
        df_market = df_market.dropna(subset=['close'])
    
    # --- Progress Tracking ---
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
            
            # Basic GVW Strict Check (combine trend and quality growth)
            # Mark as strict if trend is strict AND quality score meets threshold
            is_gvw_strict = is_strict_trend and q_val > 0.1
            
            all_stocks.append({
                'ticker': ticker,
                'name': name,
                'm_val': m_val,
                't_val': t_val,
                'q_val': q_val,
                'entry_signal': entry_signal,
                'is_gvw_strict': is_gvw_strict
            })
            
            if idx % 10 == 0 or idx == collected_size:
                print(f"Analysis Progress: {idx}/{collected_size} stocks processed...")
    
    
    if not all_stocks:
        print("No valid stock data for ranking.")
        return
    
    df_results = pd.DataFrame(all_stocks)
    df_results['m_rank'] = df_results['m_val'].rank(pct=True)
    df_results['q_rank'] = df_results['q_val'].rank(pct=True)
    df_results['t_rank'] = df_results['t_val'] 
    
    df_results['final_score'] = (df_results['m_rank'] * 0.4) + (df_results['t_rank'] * 0.3) + (df_results['q_rank'] * 0.3)
    
    # Save all results for flexible reporting
    full_results = df_results[['ticker', 'name', 'final_score', 'entry_signal', 'm_rank', 't_rank', 'q_rank']].to_dict('records')
    with open(RESULT_FILE, 'w') as f:
        json.dump(full_results, f)
    
    print(f"\nRank analysis complete. Saved {len(full_results)} stocks to {RESULT_FILE}")
    top_10 = df_results.sort_values('final_score', ascending=False).head(10)
    for i, row in enumerate(top_10.itertuples(), 1):
        print(f"{i}. {row.name} ({row.ticker}) - Score: {row.final_score:.2f} | 狀態：{row.entry_signal}")

if __name__ == "__main__":
    main()
