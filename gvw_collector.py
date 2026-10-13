import os, json, sys, pandas as pd, time, random, math, traceback
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

# Import the new abstraction layers
from src.state.state_manager import StateManager
from src.providers.twse_adapter import TWSEAdapter
from src.providers.yahoo_adapter import YahooAdapter

# ================= CONFIGURATION =================
# Use relative paths for local development/execution
DATA_DIR = 'data'
UNIVERSE_FILE = 'universe.json'
POINTER_FILE = 'pointer.json'
RESULT_FILE = 'all_rank.json'
HEALTH_STATUS_FILE = 'health_status.json'
MARKET_INDEX_FILE = 'market_index.json'
LOG_FILE = 'collector.log'

# TTL Settings (Hours)
TTL_HIGH_SCORE = 4    # Score > 0.9
TTL_MID_SCORE = 24    # Score > 0.6
TTL_LOW_SCORE = 168   # Others (7 days)

MAX_CONSECUTIVE_FAILURES = 3  # Skip stock after 3 consecutive fetch failures

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(formatted_msg + '\n')
            f.flush()
    except:
        pass

class DataProviderFactory:
    """
    Factory to provide the appropriate DataProvider with failover logic.
    """
    def __init__(self, twse_adapter: TWSEAdapter, yahoo_adapter: YahooAdapter):
        self.twse = twse_adapter
        self.yahoo = yahoo_adapter

    def fetch_all_data(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Attempts to fetch data from TWSE first, and falls back to Yahoo Finance on failure.
        """
        try:
            data = self.twse.fetch_stock_data(ticker)
            if data and not data.get('error'):
                return data
        except Exception as e:
            log(f"[FACTORY] TWSE failed for {ticker}: {e}")

        try:
            data = self.yahoo.fetch_stock_data(ticker)
            if data and not data.get('error'):
                return data
        except Exception as e:
            log(f"[FACTORY] Yahoo failed for {ticker}: {e}")
            
        return None

def get_universe():
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE, 'r') as f: return json.load(f)
    log("❌ Error: Universe file not found.")
    return {}

def load_json_safe(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f: return json.load(f)
        except: pass
    return default

def save_json(path, data):
    try:
        with open(path, 'w') as f: json.dump(data, f)
    except Exception as e:
        log(f"Error saving {path}: {e}")

def calculate_quality_score(pl_data, bs_data):
    try:
        rev_curr = float(pl_data.get('revenue', 0) or pl_data.get('營業收入', 0))
        rev_growth = 0.2 if rev_curr > 0 else 0 
        net_income = float(pl_data.get('net_income', 0) or pl_data.get('本期淨利', 0))
        margin = (net_income / rev_curr) if rev_curr > 0 else 0
        total_assets = float(bs_data.get('total_assets', 0) or bs_data.get('資產總額', 0))
        total_liab = float(bs_data.get('total_liabilities', 0) or bs_data.get('負債總額', 0))
        debt_ratio = (total_liab / total_assets) if total_assets > 0 else 1.0
        s_growth = min(1.0, max(0, (rev_growth - 0.1) / 0.3)) 
        s_margin = min(1.0, max(0, (margin - 0.05) / 0.15))
        s_stable = min(1.0, max(0, (0.6 - debt_ratio) / 0.3))
        final_q = (s_growth * 0.5) + (s_margin * 0.3) + (s_stable * 0.2)
        return round(final_q, 3)
    except Exception as e:
        log(f"Quality score calc error: {e}")
        return 0.5

def update_market_index(factory: DataProviderFactory):
    # Update TWSE Market Index
    market_stats = factory.twse.fetch_market_index("TWII")
    if market_stats:
        save_json(MARKET_INDEX_FILE, {
            "updated_at": datetime.now().isoformat(),
            "stats": market_stats
        })
        log("✅ Market index stats updated (TWSE).")

    # Update History via Yahoo
    index_history_file = os.path.join(DATA_DIR, "market_index.json")
    hist_data = factory.yahoo.fetch_market_index("^TWII")
    if hist_data:
        save_json(index_history_file, {"updated_at": datetime.now().isoformat(), "history": hist_data})
        log("✅ Market index history updated (Yahoo).")
    else:
        log("⚠️ Market index history update failed.")

def update_stock(ticker, name, provider_factory: DataProviderFactory, score=None, health_status=None, force=False):
    state_manager = StateManager(DATA_DIR, HEALTH_STATUS_FILE)
    existing_data = state_manager.get_stock_data(ticker)
    health = health_status.get(ticker, {}) if health_status else {}
    
    if health.get('status') == 'dead':
        log(f"⏭️  Skipping {ticker}: permanently dead")
        return "SKIP"
    
    if score is None:
        score = 0
    ttl_hours = TTL_HIGH_SCORE if score > 0.9 else (TTL_MID_SCORE if score > 0.6 else TTL_LOW_SCORE)
    updated_at_str = existing_data.get('updated_at', '1900-01-01T00:00:00')
    updated_at = datetime.fromisoformat(updated_at_str)
    needs_update = True if force else (updated_at + timedelta(hours=ttl_hours) < datetime.now())
    
    updated = False
    if needs_update:
        log(f"[TRACK] Fetching data for {ticker} using DataProvider...")
        new_data = provider_factory.fetch_all_data(ticker)
        
        if new_data:
            # Update financial/basic info
            existing_data['financials'] = new_data.get('financials', {})
            existing_data['basic_info'] = new_data.get('basic_info', {})
            existing_data['shareholders'] = new_data.get('shareholders', [])
            
            new_score = calculate_quality_score(
                existing_data['financials'].get('income_statement', {}),
                existing_data['financials'].get('balance_sheet', {})
            )
            
            existing_data['score'] = new_score
            existing_data['updated_at'] = datetime.now().isoformat()
            updated = True
            log(f"✅ {ticker} - Data updated (Score: {new_score}).")
        else:
            log(f"❌ {ticker} - Provider failed to return data.")
            return "FAIL"
    else:
        log(f"⏱️  {ticker}: TTL not expired.")
        
    if updated:
        state_manager.save_stock_data(ticker, existing_data)
    
    return "SUCCESS"

def main():
    # Initialize Adapters and Factory
    twse_adapter = TWSEAdapter()
    yahoo_adapter = YahooAdapter()
    factory = DataProviderFactory(twse_adapter, yahoo_adapter)
    
    state_manager = StateManager(DATA_DIR, HEALTH_STATUS_FILE)

    try:
        update_market_index(factory)
        universe_map = get_universe()
        if not universe_map: 
            log("❌ Critical Error: Universe map is empty.")
            return

        ranks_data = load_json_safe(RESULT_FILE, {'data': []})
        ranks = ranks_data.get('data', [])
        rank_map = {item['ticker']: item['final_score'] for item in ranks}
        health_status = state_manager.get_health_status()

        log("\\n--- Global Oldest-First Polling ---")
        all_stocks = []
        for t in universe_map:
            file_path = os.path.join(DATA_DIR, f"{t}.json")
            if os.path.exists(file_path):
                data = load_json_safe(file_path, {})
                updated_at = data.get('updated_at', '1900-01-01T00:00:00')
            else:
                updated_at = '1900-01-01T00:00:00'
            all_stocks.append((t, updated_at))

        all_stocks.sort(key=lambda x: x[1])
        num_to_update = math.ceil(len(all_stocks) * 0.1)
        
        target_batch = []
        for t, t_upd in all_stocks:
            if len(target_batch) >= num_to_update:
                break
            if health_status.get(t, {}).get('status') != 'dead':
                target_batch.append(t)
        
        if not target_batch:
            log("No active stocks available to update.")
            return
            
        stats = {'fetches': 0, 'failures': 0, 'skips': 0}
        for ticker in target_batch:
            result = update_stock(ticker, universe_map.get(ticker, ""), factory, 
                                  score=rank_map.get(ticker, 0), 
                                  health_status=health_status, force=True)
            if result == "SUCCESS": stats['fetches'] += 1
            elif result == "FAIL": stats['failures'] += 1
            else: stats['skips'] += 1
            
        log(f"\\nCycle complete. Stats: {stats}")

    except Exception as e:
        log(f"❌ CRITICAL ERROR in main(): {e}")
        log(traceback.format_exc())

if __name__ == "__main__":
    main()
