import os
from datetime import datetime

LOG_FILE = '/home/pi/.hermes/gvw_screener/collector.log'

def clean_log():
    if not os.path.exists(LOG_FILE):
        print("No log file found to clean.")
        return

    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Precise counting:
    # 1. Successful updates usually contain '✅'
    # 2. Let's also track how many times we had to fallback to yfinance
    success_count = 0
    fallback_count = 0
    
    for line in lines:
        if '✅' in line:
            success_count += 1
        if 'switching to yfinance track' in line:
            fallback_count += 1

    # Clear the file but keep the summary of the last period
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Log cleaned. "
                f"Last period: {success_count} stocks updated, {fallback_count} fallback to yfinance.\n")

    # Report to the user via stdout (which the Cronjob Agent will capture and send)
    print(f"🧹 Log cleaned. \n✅ Total stocks updated: {success_count}\n🔄 yfinance fallbacks: {fallback_count}")

if __name__ == "__main__":
    clean_log()
