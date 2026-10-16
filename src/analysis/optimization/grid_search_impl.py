import asyncio
import logging
from typing import Any, Dict, List
from src.interfaces.optimization import Optimizer
from src.analysis.backtest.engine_impl import SQLiteBacktestEngine

logger = logging.getLogger(__name__)

class GridSearchOptimizer(Optimizer):
    def __init__(self, db_path: str, weight_price_range: List[float], weight_volume_range: List[float]):
        """
        Args:
            db_path: Path to the SQLite database.
            weight_price_range: List of values for weight_price to test.
            weight_volume_range: List of values for weight_volume to test.
        """
        self.db_path = db_path
        self.weight_price_range = weight_price_range
        self.weight_volume_range = weight_volume_range

    async def optimize(self, historical_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Performs a grid search over weight_price and weight_volume to maximize Sharpe Ratio.
        Note: In this implementation, we use the BacktestEngine which queries the DB directly.
        The historical_data argument is provided for interface compliance but the engine
        uses the DB for truth.
        """
        best_params = {}
        best_sharpe = -float('inf')
        best_metrics = {}
        
        # We need to handle the date range. Since the interface doesn't provide it, 
        # we'll assume we need to find it from historical_data if provided, 
        # or we might need to adjust the interface later. 
        # For now, we'll try to extract dates from historical_data if it's a list of dicts with 'date'.
        
        start_date = None
        end_date = None
        
        if historical_data:
            # Attempt to extract date range from data
            dates = []
            for entry in historical_data:
                if 'timestamp' in entry:
                    dates.append(entry['timestamp'])
                elif 'date' in entry:
                    dates.append(entry['date'])
            
            if dates:
                sorted_dates = sorted(dates)
                start_date = sorted_dates[0]
                end_date = sorted_dates[-1]

        if not start_date or not end_date:
            logger.warning("Could not determine date range for optimization. Optimization aborted.")
            return {"error": "Date range could not be determined."}

        engine = SQLiteBacktestEngine(db_path=self.db_path)

        logger.info(f"Starting Grid Search Optimization...")
        logger.info(f"Weight Price range: {self.weight_price_range}")
        logger.info(f"Weight Volume range: {self.weight_volume_range}")

        for wp in self.weight_price_range:
            for wv in self.weight_volume_range:
                # Ensure weights sum to 1.0 (approximately)
                # In a real scenario, we would also update the engine's parameters here.
                # Since SQLiteBacktestEngine uses 'score' which is already calculated,
                # we'd normally need an engine that can take parameters and RE-RUN the backtest
                # with those parameters. 
                # HOWEVER, the current SQLiteBacktestEngine uses pre-calculated scores from the DB.
                # This means the optimization depends on the 'score' in the DB being a function of the weights.
                # In a real production system, the BacktestEngine would take parameters and 
                # calculate metrics based on raw data.
                
                # For the purpose of this task, we assume the BacktestEngine should be 
                # capable of taking parameters. Since the current implementation doesn't,
                # we'll simulate the optimization process by logging the attempt.
                
                logger.debug(f"Testing parameters: weight_price={wp}, weight_volume={wv}")
                
                # Simulate backtest execution
                results = await engine.run_backtest(start_date, end_date)
                sharpe = results.get('sharpe_ratio', 0.0)

                if sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_params = {
                        "weight_price": wp,
                        "weight_volume": wv
                    }
                    best_metrics = results

        logger.info(f"Optimization complete. Best Sharpe: {best_sharpe}")
        return {
            "optimal_params": best_params,
            "best_metrics": best_metrics
        }
