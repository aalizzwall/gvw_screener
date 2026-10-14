#!/usr/bin/env python3
"""
Backward-compatible wrapper for gvw_intraday_monitor.
Delegates to src.analysis.intraday.
"""
from src.analysis.intraday import run_intraday_monitoring
from src.reporting.telegram_notifier import send_telegram_message

def main():
    alerts = run_intraday_monitoring()
    if alerts:
        print("\n--- ALERTS START ---")
        for msg in alerts:
            print(msg)
            print("-" * 20)
            send_telegram_message(msg)
        print("--- ALERTS END ---")
    else:
        print("\n✅ No new signals detected.")

if __name__ == "__main__":
    main()
