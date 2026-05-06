#!/usr/bin/env python3
"""
Fetch next 100 stocks from FinMind API starting from the pointer position.
Simple batch fetch for cron jobs or one-off runs.
"""

import os, json, sys, random
from datetime import datetime, timedelta

# Load environment
ENV_PATH = '/home/pi/.hermes/.env'
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                try:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")
                except: pass

FINMIND_TOKEN = os.getenv('FINMIND_TOKEN', '')

DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
UNIVERSE_FILE = '/home/pi/.hermes/gvw_screener/universe.json'
POINTER_FILE = '/home/pi/.hermes/gvw_screener/pointer.json'

def get_finmind_data(dataset, params=None):
    """Standard FinMind v4 API wrapper with retry logic and rate limiting"""
    url = "https://api.finmindtrade.com/api/v4/data"
    params = params or {}
    params['dataset'] = dataset
    params['token'] = FINMIND_TOKEN
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            import requests
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 402:
                print(f"\n❌ Rate limit exceeded (402). Stopping.")
                sys.exit(1)
            if response.status_code == 400:
                print(f"\n❌ Token illegal or parameter error (400).")
                sys.exit(1)
            
            response.raise_for_status()
            res_json = response.json()
            return res_json['data'] if isinstance(res_json, dict) and 'data' in res_json else res_json
            
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                import time
                time.sleep(2 ** (attempt + 1))
    return None

def get_universe():
    """Load or fetch Taiwan stocks list"""
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE, 'r') as f:
            return json.load(f)
    
    print("Fetching universe from FinMind...")
    data = get_finmind_data('TaiwanStockInfo')
    if data and isinstance(data, list):
        # FILTER: Must be exactly 4 digits, numeric only, and not start with 00 or 02
        filtered_items = [item for item in data if item['stock_id'].isdigit() 
                         and len(item['stock_id']) == 4 
                         and not item['stock_id'].startswith(('00', '02'))]
        universe = {item['stock_id']: item.get('stock_name', 'Unknown') for item in filtered_items}
        with open(UNIVERSE_FILE, 'w') as f:
            json.dump(universe, f)
        print(f"✅ Universe loaded: {len(universe)} stocks")
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
        'data_id': ticker,
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

def update_stock(ticker, name, score=None):
    """Fetch and save data for a single stock"""
    file_path = os.path.join(DATA_DIR, f"{ticker}.json")
    existing_data = load_json_safe(file_path, {})
    
    updated_at = datetime.fromisoformat(existing_data.get('updated_at', '1900-01-01T00:00:00'))
    ttl_hours = 4 if score and score > 0.9 else (24 if score and score > 0.6 else 168)
    
    needs_price = (datetime.now() - updated_at) > timedelta(hours=ttl_hours)
    fin_updated_at = datetime.fromisoformat(existing_data.get('fin_updated_at', '1900-01-01T00:00:00'))
    needs_fin = (datetime.now() - fin_updated_at) > timedelta(hours=720)
    
    if not needs_price and not needs_fin:
        return "SKIP"
    
    print(f"Updating {ticker} [{'Price' if needs_price else ''} {'Fin' if needs_fin else ''}]... ", end='', flush=True)
    
    import time
    time.sleep(random.uniform(0.2, 0.5))
    
    try:
        prices = fetch_prices(ticker) if needs_price else existing_data.get('prices')
        financials = fetch_financials(ticker) if needs_fin else existing_data.get('financials')
        
        if prices and financials:
            final_data = {
                'ticker': ticker,
                'name': name,
                'updated_at': datetime.now().isoformat(),
                'fin_updated_at': datetime.now().isoformat() if needs_fin else fin_updated_at.isoformat(),
                'prices': prices,
                'financials': financials
            }
            save_json(file_path, final_data)
            print("✅")
            return "SUCCESS"
        else:
            raise ValueError("Missing data from API")
            
    except Exception as e:
        print(f"❌ {e}")
        return "FAIL"

def main():
    import random
    
    universe_map = get_universe()
    if not universe_map:
        print("❌ Failed to load universe")
        return
    
    # Load pointer
    pointer = load_json_safe(POINTER_FILE, {'last_index': 0})['last_index']
    
    # Configure batch size
    BATCH_SIZE = 100
    end_index = min(pointer + BATCH_SIZE, len(universe_map))
    batch_tickers = list(universe_map.keys())[pointer:end_index]
    
    print(f"\n📦 Fetching {len(batch_tickers)} stocks from index {pointer} to {end_index}...")
    print("=" * 60)
    
    success_count = 0
    fail_count = 0
    
    for ticker in batch_tickers:
        result = update_stock(ticker, universe_map[ticker])
        if result == "SUCCESS":
            success_count += 1
        else:
            fail_count += 1
        
        # Small delay between requests to avoid rate limits
        import time
        time.sleep(0.3)
    
    print("=" * 60)
    print(f"\n📊 Summary:")
    print(f"   Total: {len(batch_tickers)}")
    print(f"   ✅ Success: {success_count}")
    print(f"   ❌ Failed/Skip: {fail_count}")
    
    # Update pointer
    new_pointer = (pointer + BATCH_SIZE) % len(universe_map)
    save_json(POINTER_FILE, {'last_index': new_pointer})
    print(f"\n🔄 Updated pointer to: {new_pointer}")
    
    return success_count

if __name__ == "__main__":
    main()
