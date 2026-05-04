import os, json
import subprocess

# ================= CONFIGURATION =================
RESULT_FILE = '/home/pi/.hermes/gvw_screener/top_10_rank.json'

def send_telegram_message(text):
    """Schedules a message through Hermes Gateway"""
    try:
        # Use hermes CLI to send message to the home channel
        subprocess.run(['hermes', 'send', 'telegram', f"📅 **GVW 每日篩選報告**\n\n{text}"], check=True)
        return True
    except Exception as e:
        print(f"Error sending message: {e}")
        return False

def main():
    if not os.path.exists(RESULT_FILE):
        print("No rank results found.")
        return

    with open(RESULT_FILE, 'r') as f:
        top_10 = json.load(f)

    if not top_10:
        print("Top 10 list is empty.")
        return

    # Format the report with Name (Ticker), Score, Signal and Ranks
    report = []
    for i, row in enumerate(top_10, 1):
        name = row.get('name', 'Unknown')
        ticker = row.get('ticker', 'Unknown')
        score = row.get('final_score', 0)
        signal = row.get('entry_signal', '-')
        m = row.get('m_rank', 0)
        t = row.get('t_rank', 0)
        q = row.get('q_rank', 0)
        report.append(f"{i}. {name} ({ticker}) | 得分: {score:.2f} | {signal} | M:{m:.2f} T:{t:.2f} Q:{q:.2f}")
    
    final_text = "\n".join(report)
    
    print("Sending report...")
    if send_telegram_message(final_text):
        print("Report sent successfully!")
    else:
        print("Failed to send report.")

if __name__ == "__main__":
    main()
