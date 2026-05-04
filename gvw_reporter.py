import os, json, subprocess, pandas as pd
from datetime import datetime

# ================= CONFIGURATION =================
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'

def send_telegram_message(text):
    """Schedules a message through Hermes Gateway"""
    try:
        # Use hermes CLI to send message to the home channel
        subprocess.run(['hermes', 'send', 'telegram', f"📅 **GVW 每日篩選報告**\n\n{text}"], check=True)
        return True
    except Exception as e:
        print(f"Error sending message: {e}")
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
        
        # Range: from lower of (SMA20, SMA50) to upper of (SMA20, SMA50) * 1.03
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
        all_data = json.load(f)

    if not all_data:
        print("Data list is empty.")
        return

    # 1. Get overall Top 5
    sorted_all = sorted(all_data, key=lambda x: x['final_score'], reverse=True)
    top_5 = sorted_all[:5]
    
    top_5_text = "🔥 **綜合強勢 Top 5**\n"
    for i, row in enumerate(top_5, 1):
        top_5_text += f"{i}. {row['name']} ({row['ticker']}) - 得分: {row['final_score']:.2f} | {row['entry_signal']}\n"

    # 2. Get Pullback Top 5
    pullbacks = [row for row in all_data if row['entry_signal'] == '🟢 回調買入區']
    pullbacks_sorted = sorted(pullbacks, key=lambda x: x['final_score'], reverse=True)
    pullback_top_5 = pullbacks_sorted[:5]
    
    pullback_text = "\n🎯 **回調買入 Top 5 (建議價格)**\n"
    if not pullback_top_5:
        pullback_text += "目前無符合回調條件之標的\n"
    else:
        for i, row in enumerate(pullback_top_5, 1):
            price_range = get_suggested_price(row['ticker'])
            pullback_text += f"{i}. {row['name']} ({row['ticker']}) - 得分: {row['final_score']:.2f} | 區間: {price_range}\n"
    
    final_report = top_5_text + pullback_text
    
    print("Sending comprehensive report...")
    if send_telegram_message(final_report):
        print("Report sent successfully!")
    else:
        print("Failed to send report.")

if __name__ == "__main__":
    main()
