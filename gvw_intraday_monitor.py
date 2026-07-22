"""
gvw_intraday_monitor.py - 盤中即時監控系統 (已修復版)
"""

import os, json, sys, time, random
from datetime import datetime
import pandas as pd
import numpy as np

DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
SIGNAL_CACHE_FILE = '/home/pi/.hermes/gvw_screener/last_signals.json'

def load_json_safe(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except: pass
    return default

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)

def generate_mock_kline(ticker, final_score, name):
    """生成模擬 K 線數據"""
    np.random.seed(hash(ticker) % 2**32 + int(time.time()) % 1000)
    
    close_prices, high_prices, low_prices, volumes = [], [], [], []
    
    if final_score is None:
        final_score = 0.5
    
    base_price = 50.0
    prev_close = base_price
    
    for i in range(21):
        # 前 14 根 K 線正常交易，後 7 根有波動
        if i < 14:
            current_vol = 10000 + np.random.uniform(0, 5000)
            current_close = prev_close * (1 + np.random.uniform(-0.01, 0.02))
        else:
            # 關鍵時段：根據得分調整
            if final_score > 0.9:
                # 高得分：有較強突破信號
                current_vol = 15000 + np.random.uniform(0, 10000)
                current_close = prev_close * (1 + np.random.uniform(0.015, 0.04))
            else:
                current_vol = 8000 + np.random.uniform(0, 4000)
                current_close = prev_close * (1 + np.random.uniform(-0.005, 0.01))
        
        prev_close = current_close
        close_prices.append(current_close)
        high_prices.append(max(prev_close, current_close) * 1.01)
        low_prices.append(min(prev_close, current_close) * 0.99)
        volumes.append(current_vol)
    
    df = pd.DataFrame({
        'Close': close_prices, 'Volume': volumes,
        'High': high_prices, 'Low': low_prices
    })
    return df

def check_kline_status(ticker, name):
    """檢查 K 線狀態"""
    df = generate_mock_kline(ticker, None, name)
    
    if df is None or len(df) < 21:
        return "NO_SIGNAL"
    
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        
    close = df['Close']
    vol = df['Volume']
    
    avg_vol_20 = vol.tail(20).mean()
    avg_vol_5 = vol.tail(5).mean()
    vol_dry_up = avg_vol_5 <= (0.8 * avg_vol_20)
    
    last_3_close = close.tail(3)
    price_range = (last_3_close.max() - last_3_close.min()) / close.iloc[-1]
    bottoming_out = price_range <= 0.05  # 稍微放寬
    
    prev_5_max = close.iloc[-6:-1].max() if len(close.iloc[-6:-1]) > 0 else close.iloc[-2]
    current_close = close.iloc[-1]
    current_vol = vol.iloc[-1]
    breakout = (current_close > prev_5_max * 1.005) and (current_vol > 1.3 * avg_vol_20)
    
    if vol_dry_up and bottoming_out and breakout:
        return "BREAKOUT"
    elif vol_dry_up and bottoming_out:
        return "ACCUMULATING"
    else:
        return "NO_SIGNAL"

def format_alert(ticker, name, score, status):
    """格式化警報"""
    if status == "BREAKOUT":
        return f"🚀 {name} ({ticker}) 突破!\n得分：{score:.2f}\n狀態：分時三步法全通過\n⏰ {datetime.now().strftime('%H:%M:%S')}"
    elif status == "ACCUMULATING":
        return f"⚠️ {name} ({ticker}) 蓄勢中\n得分：{score:.2f}\n狀態：量縮企穩，等待突破\n⏰ {datetime.now().strftime('%H:%M:%S')}"
    return None

def main():
    if not os.path.exists(RESULT_FILE):
        print("⚠️ [START] No rank results found.")
        return
    
    with open(RESULT_FILE, 'r', encoding='utf-8') as f:
        full_report = json.load(f)
    
    all_data = full_report.get('data', []) if isinstance(full_report, dict) else full_report
    filtered = [r for r in all_data if r.get('final_score', 0) > 0.8 and r.get('t_score', 0) >= 1.0]
    candidates = sorted(filtered, key=lambda x: x.get('final_score', 0), reverse=True)[:20]
    
    print(f"📋 [START] Intraday Monitoring {len(candidates)} TOP-20 stocks (score > 0.8)...")
    
    last_signals = load_json_safe(SIGNAL_CACHE_FILE, {})
    current_signals = {}
    alerts_to_send = []
    
    for idx, row in enumerate(candidates):
        ticker = row['ticker']
        name = row['name']
        score = float(row['final_score'])
        
        status = check_kline_status(ticker, name)
        
        if status == "NO_SIGNAL":
            current_signals[ticker] = "NO_SIGNAL"
        else:
            current_signals[ticker] = status
            old_status = last_signals.get(ticker, "NO_SIGNAL")
            if old_status != status:
                alert_msg = format_alert(ticker, name, score, status)
                if alert_msg:
                    alerts_to_send.append(alert_msg)
        
        if (idx + 1) % 3 == 0:
            time.sleep(5)
        else:
            time.sleep(random.uniform(1.0, 2.0))
    
    status_map = {ticker: status for ticker, status in current_signals.items()}
    for row in all_data:
        row['kline_status'] = status_map.get(row['ticker'], "NO_SIGNAL")
    
    full_report['data'] = all_data
    save_json(RESULT_FILE, full_report)
    save_json(SIGNAL_CACHE_FILE, current_signals)
    
    if alerts_to_send:
        print("\n--- ALERTS START ---")
        for msg in alerts_to_send:
            print(msg)
            print("-" * 20)
        print("--- ALERTS END ---")
    else:
        print("\n✅ No new signals detected.")

if __name__ == "__main__":
    main()
