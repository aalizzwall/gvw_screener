import os, json, requests, pandas as pd
from datetime import datetime
from gvw_signal_detector import get_strong_buy_signals

# ================= CONFIGURATION =================
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
ENV_FILE = '/home/pi/.hermes/.env'

def load_env_var(var_name):
    """Load a specific variable from the .env file"""
    if not os.path.exists(ENV_FILE):
        return None
    with open(ENV_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{var_name}="):
                return line.split('=', 1)[1].strip()
    return None

def send_telegram_message(text):
    """Send text directly to Telegram via Bot API"""
    token = load_env_var('TELEGRAM_BOT_TOKEN')
    chat_id = load_env_var('TELEGRAM_HOME_CHANNEL')
    
    if not token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_HOME_CHANNEL not found in .env")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram API Error: {e}")
        return False

def get_suggested_price(ticker):
    """Calculate suggested buy range based on MA20/MA50"""
    file_path = os.path.join(DATA_DIR, f"{ticker}.json")
    if not os.path.exists(file_path):
        return "N/A"
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        df = pd.DataFrame(data['prices'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        
        sma20 = df['close'].rolling(window=20).mean().iloc[-1]
        sma50 = df['close'].rolling(window=50).mean().iloc[-1]
        
        low = min(sma20, sma50)
        high = max(sma20, sma50) * 1.03
        return f"{low:.1f} ~ {high:.1f}"
    except:
        return "N/A"

def main():
    if not os.path.exists(RESULT_FILE):
        print("No rank results found.")
        return
    
    with open(RESULT_FILE, 'r') as f:
        report_data = json.load(f)
    
    # Parse structure: dict with 'data' and 'last_update_time' keys
    if isinstance(report_data, dict):
        all_data = report_data.get('data', [])
        sync_health = report_data.get('earliest_data_date', 'N/A')
    else:
        # Backward compatibility for old format
        all_data = report_data
        sync_health = 'N/A'
    
    if not all_data:
        print("Data list is empty.")
        return
    
    # 1. STRONG BUY SIGNALS (K-Line 3-Step Verified)
    strong_signals = get_strong_buy_signals()
    strong_signals_text = f"📡 **數據同步健康 (最舊同步日)**: {sync_health}\n\n🚨 **強烈買入訊號 (K-line Verified)**\n"
    if not strong_signals:
        strong_signals_text += "目前無符合 K 線突破之標的，請耐心等待\n"
    else:
        for i, sig in enumerate(strong_signals, 1):
            strong_signals_text += f"{i}. {sig['name']} ({sig['ticker']}) - 得分: {sig['score']:.2f} | 狀態: 突破訊號確認!\n"
    
    # 2. Pullback Top 10 (Suggested Price)
    pullbacks = [row for row in all_data if row['entry_signal'] == '🟢 回調買入區']
    pullbacks_sorted = sorted(pullbacks, key=lambda x: x['final_score'], reverse=True)
    pullback_top_10 = pullbacks_sorted[:10]
    
    pullback_text = "\n🎯 **回調買入 Top 10 (建議價格)**\n"
    if not pullback_top_10:
        pullback_text += "目前無符合回調條件之標的\n"
    else:
        for i, row in enumerate(pullback_top_10, 1):
            gvw_tag_str = " [GVW]" if row.get('is_gvw_strict') else ""
            price_range = get_suggested_price(row['ticker'])
            pullback_text += f"{i}. {row['name']}{gvw_tag_str} ({row['ticker']}) - 得分: {row['final_score']:.2f} | 區間: {price_range}\n"
    
    # 4. GVW Strict Candidates
    gvw_strict = [row for row in all_data if row.get('is_gvw_strict')]
    gvw_strict_sorted = sorted(gvw_strict, key=lambda x: x['final_score'], reverse=True)
    
    strict_text = "\n💎 **GVW 嚴格對齊名單 (Strict)**\n"
    if not gvw_strict_sorted:
        strict_text += "目前無完全符合 GVW 嚴格標準之標的\n"
    else:
        for i, row in enumerate(gvw_strict_sorted, 1):
            price_range = get_suggested_price(row['ticker'])
            strict_text += f"{i}. {row['name']} ({row['ticker']}) | 區間: {price_range}\n"
    
    final_report = strong_signals_text + pullback_text + strict_text
    
    # ✅ Direct Push to Telegram for stability
    if send_telegram_message(final_report):
        print("Report successfully sent to Telegram via API.")
    else:
        print("Failed to send report to Telegram.")
    
    # Keep print for local logs/debug
    print("\n--- Report Content ---\n")
    print(final_report)

if __name__ == "__main__":
    main()
