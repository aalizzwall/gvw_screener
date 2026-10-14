#!/usr/bin/env python3
"""
Backward-compatible wrapper for gvw_collector.
Delegates to src.core.orchestrator.Orchestrator.
"""
from src.core.orchestrator import Orchestrator

def main():
    orchestrator = Orchestrator()
    orchestrator.run_collection_cycle()

if __name__ == "__main__":
    main()
