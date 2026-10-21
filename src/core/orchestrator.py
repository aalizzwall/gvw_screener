import os
import math
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from src.state.state_manager import StateManager
from src.providers.factory import DataProviderFactory
from src.providers.yahoo_adapter import TickerNotFoundError
from src.core.scoring_strategy import ScoringStrategy, DefaultScoringStrategy
from src.config import (
    TTL_HIGH_SCORE,
    TTL_MID_SCORE,
    TTL_LOW_SCORE
)

logger = logging.getLogger(__name__)
PRICE_TTL_HOURS = 1
YAHOO_NOT_FOUND_THRESHOLD = 3

class Orchestrator:
    """
    Manages data collection schedules, oldest-first polling batches,
    and provider failover coordination.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        provider_factory: Optional[DataProviderFactory] = None,
        scoring_strategy: Optional[ScoringStrategy] = None
    ):
        self.state = state_manager or StateManager()
        self.factory = provider_factory or DataProviderFactory()
        self.scoring_strategy = scoring_strategy or DefaultScoringStrategy()

    def calculate_quality_score(self, pl_data: Dict[str, Any], bs_data: Dict[str, Any]) -> float:
        """
        Delegates quality score calculation to the configured strategy.
        """
        return self.scoring_strategy.calculate_score(pl_data, bs_data)

    @staticmethod
    def _apply_price_history(data: Dict[str, Any], prices: List[Dict[str, Any]]) -> None:
        data["prices"] = prices
        data["price_data_as_of"] = max(str(row["date"]) for row in prices)
        data["price_data_updated_at"] = datetime.now().isoformat()
        data["price_data_source"] = prices[-1].get("source", "unknown")

    @staticmethod
    def _parse_timestamp(timestamp: str) -> datetime:
        try:
            updated_at = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            updated_at = datetime(1900, 1, 1)
        if updated_at.tzinfo is not None:
            updated_at = updated_at.astimezone().replace(tzinfo=None)
        return updated_at

    @classmethod
    def _is_expired(cls, timestamp: str, ttl_hours: int) -> bool:
        return cls._parse_timestamp(timestamp) + timedelta(hours=ttl_hours) < datetime.now()

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

    def ensure_universe(self) -> Dict[str, str]:
        universe = self.state.get_universe()
        if universe:
            return universe

        logger.info("Stock universe is empty; loading TWSE and TPEx listings.")
        universe = self.factory.fetch_universe()
        if not universe:
            raise ValueError("TWSE and TPEx returned an empty stock universe")
        self.state.save_universe(universe)
        logger.info("Initialized stock universe with %d tickers.", len(universe))
        return universe

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

        if not force and health.get('status') == 'dead':
            logger.info("⏭️  Skipping dead ticker %s; use force to retry.", ticker)
            return "SKIP"

        retry_after = health.get("retry_after")
        if not force and retry_after and self._parse_timestamp(retry_after) > datetime.now():
            logger.info("⏭️  Skipping %s until provider retry time %s.", ticker, retry_after)
            return "SKIP"

        ttl_hours = TTL_HIGH_SCORE if score > 0.9 else (TTL_MID_SCORE if score > 0.6 else TTL_LOW_SCORE)
        updated_at_str = existing_data.get('updated_at', '1900-01-01T00:00:00')
        financials_due = force or self._is_expired(
            existing_data.get('financial_data_updated_at', updated_at_str), ttl_hours
        )
        prices_due = force or self._is_expired(
            existing_data.get('price_data_updated_at', updated_at_str), PRICE_TTL_HOURS
        )

        if not financials_due and not prices_due:
            logger.info(f"⏱️  {ticker}: TTL not expired.")
            return "SKIP"

        logger.info(f"[TRACK] Fetching data for {ticker} ({name})...")
        new_data = self.factory.fetch_all_data(ticker) if financials_due else None
        yahoo_not_found = False
        if prices_due:
            try:
                prices = self.factory.fetch_price_history(ticker, days=365)
            except TickerNotFoundError:
                prices = []
                yahoo_not_found = True
        else:
            prices = []
        now = datetime.now().isoformat()
        financials_updated = bool(financials_due and new_data and not new_data.get('error'))
        prices_updated = bool(prices_due and prices)
        financials_ok = not financials_due or financials_updated
        prices_ok = not prices_due or prices_updated
        not_found_count = health.get('yahoo_not_found_count', 0)
        if yahoo_not_found:
            not_found_count = min(not_found_count + 1, YAHOO_NOT_FOUND_THRESHOLD)
        elif prices_due:
            not_found_count = 0
        is_dead = not_found_count >= YAHOO_NOT_FOUND_THRESHOLD
        existing_data.setdefault('financial_data_updated_at', updated_at_str)
        existing_data.setdefault('price_data_updated_at', updated_at_str)

        if financials_updated:
            existing_data['ticker'] = ticker
            if name:
                existing_data['name'] = name
            existing_data['financials'] = new_data.get('financials', {})
            existing_data['basic_info'] = new_data.get('basic_info', {})
            existing_data['financial_data_source'] = new_data.get('source', 'unknown')
            existing_data['financial_data_updated_at'] = new_data.get('updated_at', now)
            if 'shareholders' in new_data:
                existing_data['shareholders'] = new_data.get('shareholders', [])

        if prices_updated:
            existing_data['ticker'] = ticker
            self._apply_price_history(existing_data, prices)

        existing_data["last_refresh_attempt_at"] = now
        existing_data["last_refresh_status"] = (
            "success" if financials_ok and prices_ok
            else "partial" if financials_updated or prices_updated
            else "failed"
        )
        existing_data["provider_status"] = {
            "financials": (
                "updated" if financials_updated else "failed" if financials_due else "not_due"
            ),
            "prices": "updated" if prices_updated else "failed" if prices_due else "not_due",
        }

        if financials_updated:
            pl = existing_data.get('financials', {}).get('income_statement', {})
            bs = existing_data.get('financials', {}).get('balance_sheet', {})
            existing_data['score'] = self.calculate_quality_score(pl, bs)

        if financials_ok and prices_ok:
            existing_data['updated_at'] = now
            self.state.update_stock_health(
                ticker, {
                    'failures': 0,
                    'status': 'dead' if is_dead else 'healthy',
                    'retry_after': None,
                    'yahoo_not_found_count': not_found_count,
                }
            )
            logger.info("✅ %s - Due data refreshed successfully.", ticker)
            result = "SUCCESS"
        elif financials_updated or prices_updated:
            existing_data['updated_at'] = now
            current_failures = health.get('failures', 0) + 1
            self.state.update_stock_health(
                ticker, {
                    'failures': current_failures,
                    'status': 'dead' if is_dead else 'degraded',
                    'retry_after': None,
                    'yahoo_not_found_count': not_found_count,
                }
            )
            logger.warning(
                "⚠️ %s - Partial refresh; financials=%s, prices=%s.",
                ticker, financials_updated, prices_updated,
            )
            result = "PARTIAL"
        else:
            current_failures = health.get('failures', 0) + 1
            backoff_hours = min(2 ** min(current_failures - 1, 5), 24)
            retry_after = (
                None if is_dead
                else (datetime.now() + timedelta(hours=backoff_hours)).isoformat()
            )
            self.state.update_stock_health(ticker, {
                'failures': current_failures,
                'status': 'dead' if is_dead else 'degraded',
                'retry_after': retry_after,
                'yahoo_not_found_count': not_found_count,
            })
            if is_dead:
                logger.warning(
                    "❌ %s - Marked dead after %s consecutive Yahoo 404 checks.",
                    ticker, not_found_count,
                )
            else:
                logger.warning(
                    "❌ %s - All data providers failed (Failures: %s).",
                    ticker, current_failures,
                )
            result = "FAIL"

        self.state.save_stock_data(ticker, existing_data)
        return result

    def run_collection_cycle(self, batch_ratio: float = 0.1, force: bool = False) -> Dict[str, int]:
        """
        Runs global oldest-first polling cycle across universe.
        """
        self.update_market_index()
        universe_map = self.ensure_universe()

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
            oldest_update = min(
                self._parse_timestamp(stock_data.get('financial_data_updated_at', updated_at)),
                self._parse_timestamp(stock_data.get('price_data_updated_at', updated_at)),
            )
            all_stocks.append((t, oldest_update))

        all_stocks.sort(key=lambda x: x[1])
        num_to_update = math.ceil(len(all_stocks) * batch_ratio)

        target_batch = []
        for t, _ in all_stocks:
            if len(target_batch) >= num_to_update:
                break
            health = health_status.get(t, {})
            if not force and health.get('status') == 'dead':
                continue
            retry_after = health.get('retry_after')
            if retry_after and self._parse_timestamp(retry_after) > datetime.now():
                continue
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
            if result in ("SUCCESS", "PARTIAL"):
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
        target_tickers = tickers or list(universe_map.keys()) or self.state.list_stock_tickers()
        logger.info(f"🚀 Starting price force update for {len(target_tickers)} stocks...")
        count = 0

        for ticker in target_tickers:
            data = self.state.get_stock_data(ticker)
            try:
                prices = self.factory.fetch_price_history(ticker, days=365)
            except TickerNotFoundError:
                logger.warning("Skipping confirmed missing Yahoo ticker %s.", ticker)
                continue
            if prices:
                data['ticker'] = ticker
                self._apply_price_history(data, prices)
                data['last_refresh_attempt_at'] = datetime.now().isoformat()
                data['last_refresh_status'] = 'price_only'
                data.setdefault('provider_status', {})['prices'] = 'updated'
                self.state.save_stock_data(ticker, data)
                count += 1
                logger.info(f"✅ {ticker} prices updated ({len(prices)} bars).")
            else:
                logger.warning(f"❌ {ticker} price update failed.")

        logger.info(f"✨ Price update complete: {count}/{len(target_tickers)} stocks updated.")
        return count
