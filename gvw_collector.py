import os, json, sys, pandas as pd, time, random
from datetime import datetime, timedelta
import requests

# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
UNIVERSE_FILE = '/home/pi/.hermes/gvw_screener/universe.json'
POINTER_FILE = '/home/pi/.hermes/gvw_screener/pointer.json'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
HEALTH_STATUS_FILE = '/home/pi/.hermes/gvw_screener/health_status.json'
MARKET_INDEX_FILE = '/home/pi/.hermes/gvw_screener/market_index.json'

# TTL Settings (Hours)
TTL_HIGH_SCORE = 4    # Score > 0.9
TTL_MID_SCORE = 24    # Score > 0.6
TTL_LOW_SCORE = 168   # Others (7 days)
TTL_FINANCIALS = 720  # Financials update every 30 days
RECHECK_INTERVAL = 180 # Dead stocks re-check every 180 days

def manual_load_env(env_path='/home/pi/.hermes/.env'):
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

manual_load_env()
FINMIND_TOKEN = os.environ.get('FINMIND_TOKEN', '')

def get_finmind_data(dataset, params=None):
    """Standard FinMind v4 API wrapper with retry logic and rate limiting"""
    url = "https://api.finmindtrade.com/api/v4/data"
    params = params or {}
    params['dataset'] = dataset
    params['token'] = FINMIND_TOKEN
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 402:
                print(f"\n❌ Rate limit exceeded (402). Stopping to avoid ban.")
                sys.exit(1) 
            if response.status_code == 400:
                print(f"\n❌ Token illegal or parameter error (400).")
                sys.exit(1)
            
            response.raise_for_status()
            res_json = response.json()
            return res_json['data'] if isinstance(res_json, dict) and 'data' in res_json else res_json
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            if 500 <= status_code < 600:
                wait_time = (2 ** attempt) + random.random()
                time.sleep(wait_time)
            else:
                return None
        except Exception:
            return None
    return None

def get_universe():
    """Get Taiwan stocks list, excluding ETFs and problematic prefixes (00, 02)"""
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE, 'r') as f:
            return json.load(f)
    
    print("Fetching universe from FinMind...")
    data = get_finmind_data('TaiwanStockInfo')
    if data and isinstance(data, list):
        # FILTER: Must be exactly 4 digits, numeric only, and not start with 00 or 02
        universe = {item['stock_id']: item.get('stock_name', 'Unknown') 
                    for item in data if item['stock_id'].isdigit() 
                    and len(item['stock_id']) == 4 
                    and not item['stock_id'].startswith(('00', '02'))}
        with open(UNIVERSE_FILE, 'w') as f:
            json.dump(universe, f)
        return universe
    return {}

def load_json_safe(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f: return json.load(f)
        except: pass
    return default

def save_json(path, data):
    with open(path, 'w') as f: json.dump(data, f)

def fetch_prices(ticker):
    params = {
        'stock_id': ticker,
        'start_date': (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d'),
        'end_date': datetime.now().strftime('%Y-%m-%d')
    }
    return get_finmind_data('TaiwanStockPrice', params)

def fetch_financials(ticker):
    params = {
        'data_id': ticker,
        'start_date': (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d'),
        'end_date': datetime.now().strftime('%Y-%m-%d')
    }
    return get_finmind_data('TaiwanStockFinancialStatements', params)

def update_market_index():
    """Fetch and update TAIEX market index data once a day"""
    existing_data = load_json_safe(MARKET_INDEX_FILE, {})
    updated_at = datetime.fromisoformat(existing_data.get('updated_at', '1900-01-01T00:00:00'))
    
    if (datetime.now() - updated_at) < timedelta(days=1):
        return "SKIP"

    print("Updating TAIEX market index data...", end=' ')
    params = {
        'data_id': 'TAIEX',
        'start_date': (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d'),
        'end_date': datetime.now().strftime('%Y-%m-%d')
    }
    data = get_finmind_data('TaiwanStockPrice', params)
    if data:
        save_json(MARKET_INDEX_FILE, {
            'ticker': 'TAIEX',
            'updated_at': datetime.now().isoformat(),
            'prices': data
        })
        print("✅")
        return "SUCCESS"
    print("❌")
    return "FAIL"

def update_stock(ticker, name, score=None, health_status=None):
    """Handles split-fetching and state management for a single stock"""
    file_path = os.path.join(DATA_DIR, f"{ticker}.json")
    existing_data = load_json_safe(file_path, {})
    
    # 1. Determine TTL based on score
    if score is None: score = 0
    ttl_hours = TTL_HIGH_SCORE if score > 0.9 else (TTL_MID_SCORE if score > 0.6 else TTL_LOW_SCORE)
    
    updated_at = datetime.fromisoformat(existing_data.get('updated_at', '1900-01-01T00:00:00'))
    needs_price = (datetime.now() - updated_at) > timedelta(hours=ttl_hours)
    
    # 2. Financials TTL (Update only every 30 days)
    fin_updated_at = datetime.fromisoformat(existing_data.get('fin_updated_at', '1900-01-01T00:00:00'))
    needs_fin = (datetime.now() - fin_updated_at) > timedelta(hours=TTL_FINANCIALS)
    
    if not needs_price and not needs_fin:
        return "SKIP"

    print(f"Updating {ticker} [{'Price' if needs_price else ''} {'Fin' if needs_fin else ''}]...", end=' ')
    time.sleep(random.uniform(0.2, 0.5))
    
    try:
        prices = fetch_prices(ticker) if needs_price else existing_data.get('prices')
        financials = fetch_financials(ticker) if needs_fin else existing_data.get('financials')
        
        if prices and financials:
            # Success: Save and Reset Health
            final_data = {
                'ticker': ticker,
                'name': name,
                'updated_at': datetime.now().isoformat(),
                'fin_updated_at': datetime.now().isoformat() if needs_fin else fin_updated_at.isoformat(),
                'prices': prices,
                'financials': financials
            }
            save_json(file_path, final_data)
            if health_status is not None:
                health_status[ticker] = {'fail_count': 0, 'status': 'active'}
            print("✅")
            return "SUCCESS"
        else:
            raise ValueError("Missing data from API")
            
    except Exception as e:
        print(f"❌ {e}")
        if health_status is not None:
            count = health_status.get(ticker, {}).get('fail_count', 0) + 1
            status = 'dead' if count >= 3 else 'active'
            health_status[ticker] = {
                'fail_count': count, 
                'status': status, 
                'last_attempt_at': datetime.now().isoformat()
            }
        return "FAIL"

def main():
    # Update Market Index first
    update_market_index()
    
    universe_map = get_universe()
    if not universe_map: return
    
    ranks_data = load_json_safe(RESULT_FILE, {'data': []})
    ranks = ranks_data.get('data', [])
    rank_map = {item['ticker']: item['final_score'] for item in ranks}
    health_status = load_json_safe(HEALTH_STATUS_FILE, {})
    
    # --- TIER 1: High-Score Priority ---
    print("\n--- Phase 1: High-Score Priority ---")
    high_score_tickers = [t for t, s in rank_map.items() if s > 0.9]
    for ticker in high_score_tickers:
        if ticker in universe_map:
            update_stock(ticker, universe_map[ticker], score=rank_map[ticker], health_status=health_status)

    # --- TIER 2: Missing / Pending Repair ---
    print("\n--- Phase 2: Missing Data Repair ---")
    universe_list = list(universe_map.keys())
    missing_tickers = [t for t in universe_list if not os.path.exists(os.path.join(DATA_DIR, f"{t}.json"))]
    
    # Filter out 'dead' stocks unless they are due for a re-check
    repair_list = []
    for t in missing_tickers:
        status_info = health_status.get(t, {})
        if status_info.get('status') != 'dead':
            repair_list.append(t)
        else:
            last_attempt = status_info.get('last_attempt_at', '1900-01-01T00:00:00')
            if (datetime.now() - datetime.fromisoformat(last_attempt)).days >= RECHECK_INTERVAL:
                repair_list.append(t)
    
    # Limit repair batch to avoid API exhaustion
    for ticker in repair_list[:50]: 
        update_stock(ticker, universe_map[ticker], score=rank_map.get(ticker, 0), health_status=health_status)

    # --- TIER 3: Regular Scan (Oldest First) ---
    print("\n--- Phase 3: Regular Scan (Oldest First) ---")
    
    # Gather all existing stocks and their last update times
    stock_ages = []
    for ticker in universe_list:
        file_path = os.path.join(DATA_DIR, f"{ticker}.json")
        if os.path.exists(file_path):
            data = load_json_safe(file_path, {})
            updated_at = data.get('updated_at', '1900-01-01T00:00:00')
            stock_ages.append((ticker, updated_at))
    
    # Sort by updated_at ascending (oldest first)
    stock_ages.sort(key=lambda x: x[1])
    
    # Pick the 50 oldest stocks
    batch = [t for t, _ in stock_ages[:50]]
    
    for ticker in batch:
        update_stock(ticker, universe_map[ticker], score=rank_map.get(ticker, 0), health_status=health_status)
        
    save_json(HEALTH_STATUS_FILE, health_status)
    print(f"\nCycle complete. Updated top {len(batch)} oldest stocks.")

if __name__ == "__main__":
    main()
