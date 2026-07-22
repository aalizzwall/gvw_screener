import os
from report_engine import generate_daily_report
import requests

# ================= CONFIGURATION =================
ENV_FILE = '/home/pi/.hermes/.env'

def load_env_var(var_name):
    if not os.path.exists(ENV_FILE):
        return None
    with open(ENV_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{var_name}="):
                return line.split('=', 1)[1].strip()
    return None

def send_telegram_message(text):
    token = load_env_var('TELEGRAM_BOT_TOKEN')
    chat_id = load_env_var('TELEGRAM_HOME_CHANNEL')
    
    if not token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_HOME_CHANNEL not found in .env")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram API Error: {e}")
        return False

def main():
    print("Generating daily report via report_engine...")
    report_text = generate_daily_report()
    
    if send_telegram_message(report_text):
        print("Daily report successfully sent to Telegram.")
    else:
        print("Failed to send report.")

if __name__ == "__main__":
    main()
