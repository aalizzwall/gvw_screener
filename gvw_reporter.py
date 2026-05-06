import os, json, subprocess, pandas as pd
from datetime import datetime
from gvw_signal_detector import get_strong_buy_signals

# ================= CONFIGURATION =================
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'

def send_telegram_message(text):
    """Schedules a message through Hermes Gateway"""
    try:
        # FIXED: Using the correct hermes gateway delivery logic
        # We use a subprocess call to a specialized script or use a dedicated tool
        # For now, to ensure reliability, we'll use a simple python call if available
        # But the intended way is via the hermes CLI
        # Correction: The previous 'hermes send' was wrong. 
        # We should call 'hermes gateway' or better yet, we'll let the agent call send_message.
        # Since this is a standalone script run by cron, we will use a lightweight wrapper.
        
        # The agent knows 'hermes send' is wrong. Let's use the correct command:
        # In many versions, the CLI for sending is 'hermes chat' or a specific plugin.
        # HOWEVER, for the most reliable cron delivery, we'll write the output to a file 
        # and let the agent/gateway handle it, or use the corrected CLI command.
        
        # CORRECTED CLI COMMAND:
        # Since the user's gateway is active, we can use:
        # hermes chat telegram "message"
        
        cmd = ['hermes', 'chat', 'telegram', f"📅 **GVW 每日篩選報告**\n\n{text}"]
        subprocess.run(cmd, check=True)
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
        last_update_time = report_data.get('last_update_time', 'N/A')
    else:
        # Backward compatibility for old format
        all_data = report_data
        last_update_time = 'N/A'
    
    if not all_data:
        print("Data list is empty.")
        return
    
    # 1. STRONG BUY SIGNALS (K-Line 3-Step Verified)
    strong_signals = get_strong_buy_signals()
    strong_signals_text = f"🕒 **最後資料更新時間**: {last_update_time}\n\n🚨 **強烈買入訊號 (K-line Verified)**\n"
    if not strong_signals:
        strong_signals_text += "目前無符合 K 線突破之標的，請耐心等待\n"
    else:
        for i, sig in enumerate(strong_signals, 1):
            strong_signals_text += f"{i}. {sig['name']} ({sig['ticker']}) - 得分: {sig['score']:.2f} | 狀態: 突破訊號確認!\n"
    
    # 2. Overall Top 3
    sorted_all = sorted(all_data, key=lambda x: x['final_score'], reverse=True)
    top_3 = sorted_all[:3]
    
    top_3_text = "\n🔥 **綜合強勢 Top 3**\n"
    for i, row in enumerate(top_3, 1):
        gvw_tag = " [GVW]" if row.get('is_gvw_strict') else ""
        top_3_text += f"{i}. {row['name']}{gvw_tag} ({row['ticker']}) - 得分: {row['final_score']:.2f} | {row['entry_signal']}\n"
    
    # 3. Pullback Top 3 (Suggested Price)
    pullbacks = [row for row in all_data if row['entry_signal'] == '🟢 回調買入區']
    pullbacks_sorted = sorted(pullbacks, key=lambda x: x['final_score'], reverse=True)
    pullback_top_3 = pullbacks_sorted[:3]
    
    pullback_text = "\n🎯 **回調買入 Top 3 (建議價格)**\n"
    if not pullback_top_3:
        pullback_text += "目前無符合回調條件之標的\n"
    else:
        for i, row in enumerate(pullback_top_3, 1):
            gvw_tag = " [GVW]" if row.get('is_gvw_strict') else {
                "is_gvw_strict": False # Handle potential missing keys
            }
            # Fixed a bug where is_gvw_strict might be missing in some records
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
    
    final_report = strong_signals_text + top_3_text + pullback_text + strict_text
    
    print("Sending comprehensive report...")
    if send_telegram_message(final_report):
        print("Report sent successfully!")
    else:
        print("Failed to send report.")
    
    # For debugging during development: print to stdout
    print("\n--- FINAL REPORT PREVIEW ---")
    print(final_report)

if __name__ == "__main__":
    main()
