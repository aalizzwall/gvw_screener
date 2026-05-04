import os, json, sys, pandas as pd, numpy as np
from datetime import datetime

# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'

def calculate_momentum_score(prices_df):
    """Calculates momentum based on 3, 6, 12 month returns"""
    if len(prices_df) < 250:
        return 0
    
    current_price = prices_df['close'].iloc[-1]
    ret_3m = (current_price / prices_df['close'].iloc[-63]) - 1 if len(prices_df) >= 63 else 0
    ret_6m = (current_price / prices_df['close'].iloc[-126]) - 1 if len(prices_df) >= 126 else 0
    ret_12m = (current_price / prices_df['close'].iloc[-252]) - 1 if len(prices_df) >= 252 else 0
    
    momentum_val = (ret_3m + ret_6m + ret_12m) / 3
    return momentum_val

def calculate_trend_score(prices_df):
    """Check if price is above 200-day SMA"""
    if len(prices_df) < 200:
        return 0
    sma200 = prices_df['close'].rolling(window=200).mean().iloc[-1]
    current_price = prices_df['close'].iloc[-1]
    return 1 if current_price > sma200 else 0

def calculate_entry_signal(prices_df):
    """
    Determine entry timing based on proximity to 20-day and 50-day SMA.
    Logic: Buy when price pulls back to MA but stays above it.
    """
    if len(prices_df) < 50:
        return "數據不足"
    
    current_price = prices_df['close'].iloc[-1]
    sma20 = prices_df['close'].rolling(window=20).mean().iloc[-1]
    sma50 = prices_df['close'].rolling(window=50).mean().iloc[-1]
    
    # Distance to MAs
    dist20 = (current_price / sma20) - 1 if sma20 > 0 else 1
    dist50 = (current_price / sma50) - 1 if sma50 > 0 else 1
    
    # Signal Logic
    if 0 <= dist20 <= 0.03 or 0 <= dist50 <= 0.03:
        return "🟢 回調買入區"
    elif dist20 > 0.05:
        return "🟡 過熱-待回調"
    elif dist20 < 0:
        return "🔴 趨勢偏弱"
    else:
        return "⚪ 中立/觀察"

def calculate_quality_score(financials_data):
    """Calculate ROE manually: IncomeAfterTaxes / EquityAttributableToOwnersOfParent
    Ensures both values are positive to avoid 'negative/negative = positive' trap.
    """
    try:
        df = pd.DataFrame(financials_data)
        # Find the most recent Net Income and Total Equity
        net_income = df[df['type'] == 'IncomeAfterTaxes'].sort_values('date', ascending=False).iloc[0]['value']
        equity = df[df['type'] == 'EquityAttributableToOwnersOfParent'].sort_values('date', ascending=False).iloc[0]['value']
        
        # Quality Filter: Both Net Income and Equity must be positive
        if net_income > 0 and equity > 0:
            return float(net_income / equity)
    except Exception:
        pass
    return 0

def main():
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
        print(f"📊 數據採集進度: {collected_size} / {total_universe_size} ({progress_pct:.2f}%)")
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
            
            m_val = calculate_momentum_score(df_prices)
            t_val = calculate_trend_score(df_prices)
            q_val = calculate_quality_score(data['financials'])
            entry_signal = calculate_entry_signal(df_prices)
            
            all_stocks.append({
                'ticker': ticker,
                'name': name,
                'm_val': m_val,
                't_val': t_val,
                'q_val': q_val,
                'entry_signal': entry_signal
            })
            
            if idx % 10 == 0 or idx == collected_size:
                print(f"Analysis Progress: {idx}/{collected_size} stocks processed...")
                
        except Exception as e:
            print(f"Error processing {file}: {e}")

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
        print(f"{i}. {row.name} ({row.ticker}) - Score: {row.final_score:.2f} | 狀態: {row.entry_signal}")

if __name__ == "__main__":
    main()
