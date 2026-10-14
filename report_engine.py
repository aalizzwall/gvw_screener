#!/usr/bin/env python3
"""
Backward-compatible wrapper for report_engine.
Delegates to src.reporting.report_engine.
"""
from src.reporting.report_engine import (
    generate_daily_report,
    get_suggested_price,
    get_financials
)

if __name__ == "__main__":
    print(generate_daily_report())
