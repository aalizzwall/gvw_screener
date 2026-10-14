import os
import math
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from src.state.state_manager import StateManager
from src.providers.factory import DataProviderFactory
from src.config import (
    TTL_HIGH_SCORE,
    TTL_MID_SCORE,
    TTL_LOW_SCORE,
    MAX_CONSECUTIVE_FAILURES
)

logger = logging.getLogger(__name__)

class Orchestrator:
    """
    Manages data collection schedules, oldest-first polling batches,
    and provider failover coordination.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        provider_factory: Optional[DataProviderFactory] = None
    ):
        self.state = state_manager or StateManager()
        self.factory = provider_factory or DataProviderFactory()

    def calculate_quality_score(self, pl_data: Dict[str, Any], bs_data: Dict[str, Any]) -> float:
        """
        Calculates quality score based on revenue growth, net margin, and debt ratio.
        """
        try:
            rev_curr = float(pl_data.get('revenue', 0) or pl_data.get('營業收入', 0))
            rev_growth = 0.2 if rev_curr > 0 else 0 
            net_income = float(pl_data.get('net_income', 0) or pl_data.get('本期淨利', 0))
            margin = (net_income / rev_curr) if rev_curr > 0 else 0
            
            total_assets = float(bs_data.get('total_assets', 0) or bs_data.get('資產總額', 0))
            total_liab = float(bs_data.get('total_liabilities', 0) or bs_data.get('負債總額', 0))
            debt_ratio = (total_liab / total_assets) if total_assets > 0 else 1.0

            s_growth = min(1.0, max(0.0, (rev_growth - 0.1) / 0.3))
            s_margin = min(1.0, max(0.0, (margin - 0.05) / 0.15))
            s_stable = min(1.0, max(0.0, (0.6 - debt_ratio) / 0.3))

            final_q = (s_growth * 0.5) + (s_margin * 0.3) + (s_stable * 0.2)
            return round(final_q, 3)
        except Exception as e:
            logger.warning(f"Quality score calc error: {e}")
            return 0.5

    def update_market_index(self):
        """
        Fetches latest market index statistics and price history.
        """
        logger.info("Updating market index...")
        market_stats = self.factory.fetch_market_index("TWII")
        if market_stats:
            existing = self.state.get_market_index()
            existing.update({
                "updated_at": datetime.now().isoformat(),
                "stats": market_stats
            })
            self.state.save_market_index(existing)
            logger.info("✅ Market index stats updated.")
        else:
            logger.warning("⚠️ Market index stats update returned empty.")

    def update_stock(
        self,
        ticker: str,
        name: str = "",
        score: float = 0.0,
        health_status: Optional[Dict[str, Any]] = None,
        force: bool = False
    ) -> str:
        """
        Update single stock fundamental and financial data.
        Returns 'SUCCESS', 'SKIP', or 'FAIL'.
        """
        existing_data = self.state.get_stock_data(ticker)
        health = (health_status or self.state.get_health_status()).get(ticker, {})

        if health.get('status') == 'dead':
            logger.info(f"⏭️  Skipping {ticker}: permanently dead")
            return "SKIP"

        ttl_hours = TTL_HIGH_SCORE if score > 0.9 else (TTL_MID_SCORE if score > 0.6 else TTL_LOW_SCORE)
        updated_at_str = existing_data.get('updated_at', '1900-01-01T00:00:00')
        try:
            updated_at = datetime.fromisoformat(updated_at_str)
        except Exception:
            updated_at = datetime(1900, 1, 1)

        needs_update = True if force else (updated_at + timedelta(hours=ttl_hours) < datetime.now())

        if not needs_update:
            logger.info(f"⏱️  {ticker}: TTL not expired.")
            return "SKIP"

        logger.info(f"[TRACK] Fetching data for {ticker} ({name})...")
        new_data = self.factory.fetch_all_data(ticker)

        if new_data and not new_data.get('error'):
            existing_data['ticker'] = ticker
            if name:
                existing_data['name'] = name
            existing_data['financials'] = new_data.get('financials', {})
            existing_data['basic_info'] = new_data.get('basic_info', {})
            if 'shareholders' in new_data:
                existing_data['shareholders'] = new_data.get('shareholders', [])

            # Preserve or fetch prices if empty
            if 'prices' not in existing_data or not existing_data['prices']:
                prices = self.factory.fetch_price_history(ticker, days=365)
                if prices:
                    existing_data['prices'] = prices

            # Calculate Quality Score
            pl = existing_data.get('financials', {}).get('income_statement', {})
            bs = existing_data.get('financials', {}).get('balance_sheet', {})
            new_score = self.calculate_quality_score(pl, bs)

            existing_data['score'] = new_score
            existing_data['updated_at'] = datetime.now().isoformat()
            self.state.save_stock_data(ticker, existing_data)

            # Reset health failure count
            self.state.update_stock_health(ticker, {'failures': 0, 'status': 'active'})
            logger.info(f"✅ {ticker} - Data updated (Quality Score: {new_score}).")
            return "SUCCESS"
        else:
            # Handle failure
            current_failures = health.get('failures', 0) + 1
            status = 'dead' if current_failures >= MAX_CONSECUTIVE_FAILURES else 'degraded'
            self.state.update_stock_health(ticker, {'failures': current_failures, 'status': status})
            logger.warning(f"❌ {ticker} - Provider failed to return data (Failures: {current_failures}).")
            return "FAIL"

    def run_collection_cycle(self, batch_ratio: float = 0.1, force: bool = False) -> Dict[str, int]:
        """
        Runs global oldest-first polling cycle across universe.
        """
        self.update_market_index()
        universe_map = self.state.get_universe()

        if not universe_map:
            logger.error("❌ Critical Error: Universe map is empty.")
            return {'fetches': 0, 'failures': 0, 'skips': 0}

        ranks_data = self.state.get_ranks()
        ranks = ranks_data.get('data', [])
        rank_map = {item['ticker']: item.get('final_score', 0) for item in ranks}
        health_status = self.state.get_health_status()

        logger.info("\n--- Global Oldest-First Polling ---")
        all_stocks = []
        for t in universe_map:
            stock_data = self.state.get_stock_data(t)
            updated_at = stock_data.get('updated_at', '1900-01-01T00:00:00')
            all_stocks.append((t, updated_at))

        all_stocks.sort(key=lambda x: x[1])
        num_to_update = math.ceil(len(all_stocks) * batch_ratio)

        target_batch = []
        for t, _ in all_stocks:
            if len(target_batch) >= num_to_update:
                break
            if health_status.get(t, {}).get('status') != 'dead':
                target_batch.append(t)

        if not target_batch:
            logger.info("No active stocks available to update.")
            return {'fetches': 0, 'failures': 0, 'skips': 0}

        stats = {'fetches': 0, 'failures': 0, 'skips': 0}
        total = len(target_batch)
        for i, ticker in enumerate(target_batch, 1):
            name = universe_map.get(ticker, "")
            score = rank_map.get(ticker, 0)
            result = self.update_stock(ticker, name=name, score=score, health_status=health_status, force=force)
            if result == "SUCCESS":
                stats['fetches'] += 1
            elif result == "FAIL":
                stats['failures'] += 1
            else:
                stats['skips'] += 1

            if i % 20 == 0 or i == total:
                logger.info(f"Progress: {i}/{total} stocks processed...")

        logger.info(f"\nCycle complete. Stats: {stats}")
        return stats

    def force_update_prices(self, tickers: Optional[List[str]] = None) -> int:
        """
        Forces update of price history for all or specified stocks.
        """
        universe_map = self.state.get_universe()
        target_tickers = tickers or list(universe_map.keys()) or self.state.list_stock_files()
        logger.info(f"🚀 Starting price force update for {len(target_tickers)} stocks...")
        count = 0

        for ticker in target_tickers:
            data = self.state.get_stock_data(ticker)
            prices = self.factory.fetch_price_history(ticker, days=365)
            if prices:
                data['ticker'] = ticker
                data['prices'] = prices
                data['updated_at'] = datetime.now().isoformat()
                self.state.save_stock_data(ticker, data)
                count += 1
                logger.info(f"✅ {ticker} prices updated ({len(prices)} bars).")
            else:
                logger.warning(f"❌ {ticker} price update failed.")

        logger.info(f"✨ Price update complete: {count}/{len(target_tickers)} stocks updated.")
        return count

