import os, json, sys, pandas as pd, time, random
from datetime import datetime, timedelta
import requests


# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
UNIVERSE_FILE = '/home/pi/.hermes/gvw_screener/universe.json'
POINTER_FILE = '/home/pi/.hermes/gvw_screener/pointer.json'

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
            
            # 1. Handle Rate Limits (402) or Token Issues (400)
            if response.status_code == 402:
                print(f"\n❌ Rate limit exceeded (402). Stopping batch to avoid ban.")
                sys.exit(1) 
            if response.status_code == 400:
                print(f"\n❌ Token illegal or parameter error (400). Please check .env.")
                sys.exit(1)
            
            response.raise_for_status()
            res_json = response.json()
            if isinstance(res_json, dict) and 'data' in res_json:
                return res_json['data']
            return res_json
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            # 2. Retry only on 5xx Server Errors using Exponential Backoff
            if 500 <= status_code < 600:
                wait_time = (2 ** attempt) + random.random()
                print(f"⚠️ Server Error {status_code}. Retrying in {wait_time:.2f}s...")
                time.sleep(wait_time)
            else:
                print(f"API Error (dataset={dataset}): {e}")
                return None
        except Exception as e:
            print(f"Unexpected Error (dataset={dataset}): {e}")
            return None
            
    print(f"Max retries reached for dataset {dataset}.")
    return None

def get_universe():
    """Get all Taiwan stocks list with names, excluding ETFs/Funds (00 prefix)"""
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE, 'r') as f:
            return json.load(f)
    
    print("Fetching universe from FinMind (TaiwanStockInfo)...")
    data = get_finmind_data('TaiwanStockInfo')
    if data and isinstance(data, list):
        # Store as a dictionary: {stock_id: stock_name}
        # FILTER: Exclude tickers starting with '00' (ETFs, Funds, etc.)
        universe = {item['stock_id']: item.get('stock_name', 'Unknown') 
                    for item in data if not item['stock_id'].startswith('00')}
        with open(UNIVERSE_FILE, 'w') as f:
            json.dump(universe, f)
        print(f"Universe updated: {len(universe)} eligible stocks found (ETFs excluded).")
        return universe
    print(f"Unexpected data format for universe: {type(data)}")
    return {}

def get_stock_data(ticker):
    """Fetch OHLCV and Financials for a single stock using v4 datasets"""
    price_params = {
        'data_id': ticker,
        'start_date': (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d'),
        'end_date': datetime.now().strftime('%Y-%m-%d')
    }
    prices = get_finmind_data('TaiwanStockPrice', price_params)
    
    fin_params = {
        'data_id': ticker,
        'start_date': (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d'),
        'end_date': datetime.now().strftime('%Y-%m-%d')
    }
    financials = get_finmind_data('TaiwanStockFinancialStatements', fin_params)
    
    return {
        'prices': prices,
        'financials': financials
    }

def main():
    universe_map = get_universe()
    if not universe_map:
        print("Failed to get universe.")
        return

    universe_list = list(universe_map.keys())
    
    pointer = 0
    if os.path.exists(POINTER_FILE):
        with open(POINTER_FILE, 'r') as f:
            pointer = json.load(f).get('last_index', 0)

    print(f"Current pointer: {pointer}. Total stocks: {len(universe_list)}")

    batch_size = 100
    end_index = min(pointer + batch_size, len(universe_list))
    batch = universe_list[pointer:end_index]

    for ticker in batch:
        file_path = os.path.join(DATA_DIR, f"{ticker}.json")
        
        should_update = True
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                try:
                    data = json.load(f)
                    updated_at = datetime.fromisoformat(data.get('updated_at', '1900-01-01T00:00:00'))
                    if datetime.now() - updated_at < timedelta(hours=24):
                        should_update = False
                except:
                    should_update = True
        
        if should_update:
            print(f"Fetching data for {ticker}...", end=' ')
            # Anti-Throttling: Small random sleep before each stock
            time.sleep(random.uniform(0.2, 0.6))
            stock_data = get_stock_data(ticker)
            if stock_data['prices'] and stock_data['financials']:
                final_data = {
                    'ticker': ticker,
                    'name': universe_map.get(ticker, 'Unknown'),
                    'updated_at': datetime.now().isoformat(),
                    'prices': stock_data['prices'],
                    'financials': stock_data['financials']
                }
                with open(file_path, 'w') as f:
                    json.dump(final_data, f)
                print("✅")
            else:
                print("❌ (Price: " + ("OK" if stock_data['prices'] else "FAIL") + 
                      ", Fin: " + ("OK" if stock_data['financials'] else "FAIL") + ")")
        else:
            print(f"Data for {ticker} is still fresh. ⏩")

    new_pointer = end_index if end_index < len(universe_list) else 0
    with open(POINTER_FILE, 'w') as f:
        json.dump({'last_index': new_pointer}, f)

    print(f"\nBatch complete. Next pointer: {new_pointer}")

if __name__ == "__main__":
    main()
