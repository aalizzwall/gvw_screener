import argparse
import asyncio
import logging
import sys
from typing import Any, Dict, List, Optional

from src.analysis.backtest.engine_impl import SQLiteBacktestEngine
from src.analysis.engine_impl import GVWEngineImpl
from src.analysis.optimization.grid_search_impl import GridSearchOptimizer
from src.orchestrator import Orchestrator
from src.persistence.sqlite_repository import SQLiteRepository
from src.providers.yahoo_adapter import YahooAdapter


async def run_backtest_command(args: argparse.Namespace) -> None:
    """Runs the backtest engine."""
    logger = logging.getLogger("Backtest")
    repository = SQLiteRepository(db_path=args.db)
    await repository.connect()

    engine = SQLiteBacktestEngine(db_path=args.db)

    logger.info("Starting backtest from %s to %s...", args.start_date, args.end_date)
    results = await engine.run_backtest(args.start_date, args.end_date)

    print("\n--- Backtest Results ---")
    print(f"Sample Size:    {results.get('sample_size')}")
    print(f"Win Rate:       {results.get('win_rate', 0.0):.2%}")
    print(f"Sharpe Ratio:   {results.get('sharpe_ratio', 0.0):.2f}")
    print(f"Max Drawdown:   {results.get('max_drawdown', 0.0):.2%}")
    print(f"Total Return:   {results.get('total_return', 0.0):.2%}")
    print("------------------------\n")

    await repository.close()


async def run_optimize_command(args: argparse.Namespace) -> None:
    """Runs the optimization process."""
    logger = logging.getLogger("Optimization")
    repository = SQLiteRepository(db_path=args.db)
    await repository.connect()

    weight_price_range = [0.1, 0.3, 0.5, 0.7, 0.9]
    weight_volume_range = [0.1, 0.3, 0.5, 0.7, 0.9]

    optimizer = GridSearchOptimizer(
        db_path=args.db,
        weight_price_range=weight_price_range,
        weight_volume_range=weight_volume_range,
    )

    start_date = args.start_date
    end_date = args.end_date

    logger.info("Starting optimization from %s to %s...", start_date, end_date)

    historical_data = [{"timestamp": start_date}, {"timestamp": end_date}]
    results = await optimizer.optimize(historical_data)

    if "error" in results:
        print(f"Optimization failed: {results['error']}")
    else:
        print("\n--- Optimization Results ---")
        opt_params = results.get("optimal_params", {})
        best_metrics = results.get("best_metrics", {})
        print(f"Optimal Weight Price:  {opt_params.get('weight_price')}")
        print(f"Optimal Weight Volume: {opt_params.get('weight_volume')}")
        print(f"Best Sharpe Ratio:     {best_metrics.get('sharpe_ratio', 0.0):.2f}")
        print(f"Best Total Return:     {best_metrics.get('total_return', 0.0):.2%}")
        print("----------------------------\n")

    await repository.close()


async def run_screen_command(args: argparse.Namespace) -> None:
    """Runs the streaming screen process."""
    logger = logging.getLogger("Screen")
    adapter = YahooAdapter()
    engine = GVWEngineImpl(strategy_params={"weight_price": 0.6, "weight_volume": 0.4})
    repository = SQLiteRepository(db_path=args.db)
    await repository.connect()

    orchestrator = Orchestrator(data_provider=adapter, engine=engine, repository=repository)
    try:
        await orchestrator.run(args.symbols)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        await orchestrator.close()
        await repository.close()


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds and returns the CLI argument parser."""
    parser = argparse.ArgumentParser(description="GVW Screener CLI")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--db", type=str, default="scoring_results.db", help="Path to SQLite database")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Screen command
    screen_parser = subparsers.add_parser("screen", help="Run the screening process")
    screen_parser.add_argument(
        "--symbols",
        nargs="+",
        default=["AAPL", "MSFT", "GOOGL", "TSLA", "AMZN"],
        help="List of symbols to screen",
    )

    # Backtest command
    backtest_parser = subparsers.add_parser("backtest", help="Run the backtesting engine")
    backtest_parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    backtest_parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")

    # Optimize command
    optimize_parser = subparsers.add_parser("optimize", help="Run the parameter optimization")
    optimize_parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    optimize_parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")

    return parser


async def async_main(args: argparse.Namespace) -> None:
    """Async dispatch for CLI subcommands."""
    if args.command == "backtest":
        await run_backtest_command(args)
    elif args.command == "optimize":
        await run_optimize_command(args)
    elif args.command == "screen":
        await run_screen_command(args)
    else:
        build_arg_parser().print_help()


def main(argv: Optional[List[str]] = None) -> None:
    """Synchronous entrypoint for CLI execution."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
