import os, json, sys, pandas as pd
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
    """Standard FinMind v4 API wrapper using the /data endpoint"""
    url = "https://api.finmindtrade.com/api/v4/data"
    params = params or {}
    params['dataset'] = dataset
    params['token'] = FINMIND_TOKEN
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        res_json = response.json()
        if isinstance(res_json, dict) and 'data' in res_json:
            return res_json['data']
        return res_json
    except Exception as e:
        print(f"API Error (dataset={dataset}): {e}")
        return None

def get_universe():
    """Get all Taiwan stocks list with names using TaiwanStockInfo dataset"""
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE, 'r') as f:
            return json.load(f)
    
    print("Fetching universe from FinMind (TaiwanStockInfo)...")
    data = get_finmind_data('TaiwanStockInfo')
    if data and isinstance(data, list):
        # Store as a dictionary: {stock_id: stock_name}
        universe = {item['stock_id']: item.get('stock_name', 'Unknown') for item in data}
        with open(UNIVERSE_FILE, 'w') as f:
            json.dump(universe, f)
        print(f"Universe updated: {len(universe)} stocks found.")
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
