#!/usr/bin/env python3
"""
Backward-compatible wrapper for full_update.
Delegates to Orchestrator, Ranker, and Reporter pipeline.
"""
from src.core.orchestrator import Orchestrator
from src.analysis.ranker import run_ranking
from src.reporting.report_engine import generate_daily_report
from src.reporting.telegram_notifier import send_telegram_message

def main():
    print("🚀 Starting Full Pipeline Update...")
    orchestrator = Orchestrator()
    orchestrator.run_collection_cycle(batch_ratio=1.0, force=True)
    run_ranking()
    report_text = generate_daily_report()
    send_telegram_message(report_text)
    print("✨ Full pipeline update finished.")

if __name__ == "__main__":
    main()
