#!/usr/bin/env python3
"""
Backward-compatible wrapper for gvw_reporter.
Delegates to src.reporting.
"""
from src.reporting.report_engine import generate_daily_report
from src.reporting.telegram_notifier import send_telegram_message

def main():
    print("Generating daily report via src.reporting...")
    report_text = generate_daily_report()
    if send_telegram_message(report_text):
        print("Daily report successfully sent to Telegram.")
    else:
        print("Telegram not configured or send failed. Printing report below:\n")
        print(report_text)

if __name__ == "__main__":
    main()
