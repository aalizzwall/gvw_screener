import asyncio # Testing edit
import logging
import sys
import argparse
from src.providers.yahoo_adapter import YahooAdapter
from src.analysis.engine_impl import GVWEngineImpl
from src.orchestrator import Orchestrator
from src.persistence.sqlite_repository import SQLiteRepository
from src.analysis.backtest.engine_impl import SQLiteBacktestEngine
from src.analysis.optimization.grid_search_impl import GridSearchOptimizer

async def run_backtest_command(args):
    """Runs the backtest engine."""
    logger = logging.getLogger("Backtest")
    repository = SQLiteRepository(db_path=args.db)
    await repository.connect()
    
    engine = SQLiteBacktestEngine(db_path=args.db)
    
    logger.info(f"Starting backtest from {args.start_date} to {args.end_date}...")
    results = await engine.run_backtest(args.start_date, args.end_date)
    
    print("\n--- Backtest Results ---")
    print(f"Sample Size:    {results.get('sample_size')}")
    print(f"Win Rate:       {results.get('win_rate'):.2%}")
    print(f"Sharpe Ratio:   {results.get('sharpe_ratio'):.2f}")
    print(f"Max Drawdown:   {results.get('max_drawdown'):.2%}")
    print(f"Total Return:   {results.get('total_return'):.2%}")
    print("------------------------\n")
    
    await repository.close()

async def run_optimize_command(args):
    """Runs the optimization process."""
    logger = logging.getLogger("Optimization")
    repository = SQLiteRepository(db_path=args.db)
    await repository.connect()

    # Define optimization parameters
    weight_price_range = [0.1, 0.3, 0.5, 0.7, 0.9]
    weight_volume_range = [0.1, 0.3, 0.5, 0.7, 0.9]

    optimizer = GridSearchOptimizer(
        db_path=args.db,
        weight_price_range=weight_price_range,
        weight_volume_range=weight_volume_range
    )

    # Use the provided dates
    start_date = args.start_date
    end_date = args.end_date

    logger.info(f"Starting optimization from {start_date} to {end_date}...")
    
    # We'll pass a dummy list of dicts with dates to satisfy the interface
    historical_data = [{"timestamp": start_date}, {"timestamp": end_date}]
    
    results = await optimizer.optimize(historical_data)
    
    if "error" in results:
        print(f"Optimization failed: {results['error']}")
    else:
        print("\n--- Optimization Results ---")
        print(f"Optimal Weight Price: {results['optimal_params'].get('weight_price')}")
        print(f"Optimal Weight Volume: {results['optimal_params'].get('weight_volume')}")
        print(f"Best Sharpe Ratio:    {results['best_metrics'].get('sharpe_ratio'):.2f}")
        print(f"Best Total Return:    {results['best_metrics'].get('total_return'):.2%}")
        print("----------------------------\n")

    await repository.close()

async def main():
    parser = argparse.ArgumentParser(description="GVW Screener CLI")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--db", type=str, default="scoring_results.db", help="Path to SQLite database")
    
    # Add subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Screen command
    screen_parser = subparsers.add_parser("screen", help="Run the screening process")
    screen_parser.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "GOOGL", "TSLA", "AMZN"], 
                               help="List of symbols to screen")

    # Backtest command
    backtest_parser = subparsers.add_parser("backtest", help="Run the backtesting engine")
    backtest_parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    backtest_parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")

    # Optimize command
    optimize_parser = subparsers.add_parser("optimize", help="Run the parameter optimization")
    optimize_parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    optimize_parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger("CLI")

    if args.command == "backtest":
        await run_backtest_command(args)
    elif args.command == "optimize":
        await run_optimize_command(args)
    elif args.command == "screen":
        # 1. Initialize the chosen Adapter
        adapter = YahooAdapter()
        
        # 2. Initialize the GVW Engine
        engine = GVWEngineImpl(strategy_params={"weight_price": 0.6, "weight_volume": 0.4})

        # 3. Initialize the SQLite Repository
        repository = SQLiteRepository(db_path=args.db)
        await repository.connect()
        
        # 4. Initialize the Orchestrator
        orchestrator = Orchestrator(data_provider=adapter, engine=engine, repository=repository)

        try:
            # 5. Start the process
            await orchestrator.run(args.symbols)
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
        finally:
            # 6. Cleanup
            await orchestrator.close()
            await repository.close()
    else:
        parser.print_help()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

async def run_backtest_command(args):
    """Runs the backtest engine."""
    logger = logging.getLogger("Backtest")
    repository = SQLiteRepository(db_path=args.db)
    await repository.connect()
    
    engine = SQLiteBacktestEngine(db_path=args.db)
    
    logger.info(f"Starting backtest from {args.start_date} to {args.end_date}...")
    results = await engine.run_backtest(args.start_date, args.end_date)
    
    print("\n--- Backtest Results ---")
    print(f"Sample Size:    {results.get('sample_size')}")
    print(f"Win Rate:       {results.get('win_rate'):.2%}")
    print(f"Sharpe Ratio:   {results.get('sharpe_ratio'):.2f}")
    print(f"Max Drawdown:   {results.get('max_drawdown'):.2%}")
    print(f"Total Return:   {results.get('total_return'):.2%}")
    print("------------------------\n")
    
    await repository.close()

async def main():
    parser = argparse.ArgumentParser(description="GVW Screener CLI")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--db", type=str, default="scoring_results.db", help="Path to SQLite database")
    
    # Add subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Screen command
    screen_parser = subparsers.add_parser("screen", help="Run the screening process")
    screen_parser.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "GOOGL", "TSLA", "AMZN"], 
                               help="List of symbols to screen")

    # Backtest command
    backtest_parser = subparsers.add_parser("backtest", help="Run the backtesting engine")
    backtest_parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    backtest_parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger("CLI")

    if args.command == "backtest":
        await run_backtest_command(args)
    elif args.command == "screen":
        # 1. Initialize the chosen Adapter
        adapter = YahooAdapter()
        
        # 2. Initialize the GVW Engine
        engine = GVWEngineImpl(strategy_params={"weight_price": 0.6, "weight_volume": 0.4})

        # 3. Initialize the SQLite Repository
        repository = SQLiteRepository(db_path=args.db)
        await repository.connect()
        
        # 4. Initialize the Orchestrator
        orchestrator = Orchestrator(data_provider=adapter, engine=engine, repository=repository)

        try:
            # 5. Start the process
            await orchestrator.run(args.symbols)
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
        finally:
            # 6. Cleanup
            await orchestrator.close()
            await repository.close()
    else:
        parser.print_help()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
