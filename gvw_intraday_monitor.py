import os, json, subprocess, pandas as pd
from datetime import datetime
from gvw_signal_detector import check_kline_three_steps

# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
SIGNAL_CACHE_FILE = '/home/pi/.hermes/gvw_screener/last_signals.json'

def send_telegram_alert(ticker, name, score):
    """Send an immediate alert to Telegram via Hermes Gateway"""
    msg = (
        f"🚨 **GVW 即時突破警報**\n\n"
        f"標的: {name} ({ticker})\n"
        f"強度得分: {score:.2f}\n"
        f"狀態: K線三步法驗證通過！\n\n"
        f"建議立即檢查分時圖與掛單區間。"
    )
    try:
        cmd = ['hermes', 'chat', 'telegram', msg]
        subprocess.run(cmd, check=True)
        return True
    except Exception as e:
        print(f"Error sending alert: {e}")
        return False

def load_json_safe(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f: return json.load(f)
        except: pass
    return default

def save_json(path, data):
    with open(path, 'w') as f: json.dump(data, f)

def main():
    # 1. Load Rank Results
    if not os.path.exists(RESULT_FILE):
        print("No rank results found.")
        return
    
    with open(RESULT_FILE, 'r') as f:
        report_data = json.load(f)
    
    all_data = report_data.get('data', []) if isinstance(report_data, dict) else report_data
    if not all_data:
        return

    # 2. Load Signal Cache (to prevent spam)
    last_signals = load_json_safe(SIGNAL_CACHE_FILE, {})
    
    # 3. Filter Candidates: Score >= 0.95 AND Pullback Zone
    candidates = [row for row in all_data if row['final_score'] >= 0.95 and row['entry_signal'] == '🟢 回調買入區']
    
    if not candidates:
        print("No high-score pullback candidates found for monitoring.")
        return

    print(f"Monitoring {len(candidates)} high-potential stocks...")
    
    new_signals_found = 0
    current_signals = {}

    for row in candidates:
        ticker = row['ticker']
        name = row['name']
        score = row['final_score']
        
        # Run K-line 3-step verification
        res = check_kline_three_steps(ticker)
        
        if res and res['all_passed']:
            current_signals[ticker] = datetime.now().isoformat()
            
            # Check if this is a NEW signal (not alerted in the last 24h)
            last_alerted = last_signals.get(ticker)
            if not last_alerted:
                print(f"🔥 SIGNAL FOUND: {ticker} {name}")
                if send_telegram_alert(ticker, name, score):
                    new_signals_found += 1
            else:
                print(f"Stock {ticker} is still in signal zone (already alerted).")
        else:
            # If it was previously a signal but now isn't, we could remove it from cache
            # but for simplicity, we keep it until a manual reset or long expiry.
            pass

    # Update signal cache
    # Merge current signals with old ones to keep track of all active alerts
    updated_cache = {**last_signals, **current_signals}
    save_json(SIGNAL_CACHE_FILE, updated_cache)
    
    print(f"Intraday scan complete. New alerts sent: {new_signals_found}")

if __name__ == "__main__":
    main()
