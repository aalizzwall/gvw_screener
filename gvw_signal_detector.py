#!/usr/bin/env python3
"""
Backward-compatible wrapper for gvw_signal_detector.
Delegates to src.analysis.signals.
"""
from src.analysis.signals import get_strong_buy_signals, check_kline_three_steps

if __name__ == "__main__":
    signals = get_strong_buy_signals()
    print(f"Found {len(signals)} strong buy signals: {signals}")
