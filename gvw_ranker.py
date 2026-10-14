#!/usr/bin/env python3
"""
Backward-compatible wrapper for gvw_ranker.
Delegates to src.analysis.ranker.run_ranking.
"""
from src.analysis.ranker import run_ranking

def main():
    run_ranking()

if __name__ == "__main__":
    main()
