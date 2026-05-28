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

# Load FINMIND token from environment variable
FINMIND_TOKEN = os.getenv('FINMIND_TOKEN', '').strip()

print(f"✅ FINMIND_TOKEN loaded: {len(FINMIND_TOKEN) if FINMIND_TOKEN else 0} chars")

def get_finmind_data(dataset, params=None):
    """Standard FinMind v4 API wrapper with retry logic and rate limiting"""
    url = "https://api.finmindtrade.com/api/v4/data"
    query_params = {'dataset': dataset}
    if params:
        for key, value in params.items():
            query_params[key] = value
    if 'start_date' not in query_params:
        query_params['start_date'] = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    if 'end_date' not in query_params:
        query_params['end_date'] = datetime.now().strftime('%Y-%m-%d')
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            headers = {}
            if FINMIND_TOKEN and len(FINMIND_TOKEN) >= 170:
                headers['Authorization'] = f'Bearer {FINMIND_TOKEN}'
            
            response = requests.get(url, params=query_params, headers=headers, timeout=15)
            if response.status_code == 402:
                print(f"\n❌ Rate limit exceeded (402). Stopping to avoid ban.")
                sys.exit(1)
            if response.status_code == 400 and 'msg' in response.json() and 'Token is illegal' in response.json().get('msg', ''):
                continue
            
            response.raise_for_status()
            res_json = response.json()
            return res_json['data'] if isinstance(res_json, dict) and 'data' in res_json else res_json
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            if 500 <= status_code < 600:
                time.sleep((2 ** attempt) + random.random())
            else:
                return None
        except Exception:
            return None
    return None

def get_universe():
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE, 'r') as f: return json.load(f)
    data = get_finmind_data('TaiwanStockInfo')
    if data and isinstance(data, list):
        universe = {item['stock_id']: item.get('stock_name', 'Unknown')
                    for item in data if item['stock_id'].isdigit()
                    and len(item['stock_id']) == 4
                    and not item['stock_id'].startswith(('00', '02'))}
        with open(UNIVERSE_FILE, 'w') as f: json.dump(universe, f)
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
    params = {'stock_id': ticker, 'start_date': (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d'), 'end_date': datetime.now().strftime('%Y-%m-%d')}
    return get_finmind_data('TaiwanStockPrice', params)

def fetch_financials(ticker):
    params = {'data_id': ticker, 'start_date': (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d'), 'end_date': datetime.now().strftime('%Y-%m-%d')}
    return get_finmind_data('TaiwanStockFinancialStatements', params)

def update_market_index():
    existing_data = load_json_safe(MARKET_INDEX_FILE, {})
    updated_at = datetime.fromisoformat(existing_data.get('updated_at', '1900-01-01T00:00:00'))
    if (datetime.now() - updated_at) < timedelta(days=1): return "SKIP"
    data = get_finmind_data('TaiwanStockPrice', {'data_id': 'TAIEX', 'start_date': (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d'), 'end_date': datetime.now().strftime('%Y-%m-%d')})
    if data:
        save_json(MARKET_INDEX_FILE, {'ticker': 'TAIEX', 'updated_at': datetime.now().isoformat(), 'prices': data})
        return "SUCCESS"
    return "FAIL"

def update_stock(ticker, name, score=None, health_status=None):
    file_path = os.path.join(DATA_DIR, f"{ticker}.json")
    existing_data = load_json_safe(file_path, {})
    if score is None: score = 0
    ttl_hours = TTL_HIGH_SCORE if score > 0.9 else (TTL_MID_SCORE if score > 0.6 else TTL_LOW_SCORE)
    updated_at = datetime.fromisoformat(existing_data.get('updated_at', '1900-01-01T00:00:00'))
    needs_price = (datetime.now() - updated_at) > timedelta(hours=ttl_hours)
    fin_updated_at = datetime.fromisoformat(existing_data.get('fin_updated_at', '1900-01-01T00:00:00'))
    needs_fin = (datetime.now() - fin_updated_at) > timedelta(hours=TTL_FINANCIALS)
    if not needs_price and not needs_fin: return "SKIP"

    time.sleep(random.uniform(0.2, 0.5))
    try:
        new_prices = fetch_prices(ticker) if needs_price else None
        new_fins = fetch_financials(ticker) if needs_fin else None
        updated = False
        if new_prices:
            existing_data['prices'] = new_prices
            updated = True
        if new_fins:
            existing_data['financials'] = new_fins
            existing_data['fin_updated_at'] = datetime.now().isoformat()
            updated = True
        if updated:
            existing_data['updated_at'] = datetime.now().isoformat()
            existing_data['ticker'] = ticker
            existing_data['name'] = name
            save_json(file_path, existing_data)
            if health_status is not None:
                # SUCCESS immediately resets the stock to active
                health_status[ticker] = {'fail_count': 0, 'status': 'active'}
            return "SUCCESS"
        else:
            if needs_price or needs_fin:
                existing_data['updated_at'] = datetime.now().isoformat()
                existing_data['fin_updated_at'] = datetime.now().isoformat()
                save_json(file_path, existing_data)
            return "SKIP"
    except Exception:
        if health_status is not None:
            count = health_status.get(ticker, {}).get('fail_count', 0) + 1
            health_status[ticker] = {'fail_count': count, 'status': 'dead' if count >= 3 else 'active', 'last_attempt_at': datetime.now().isoformat()}
        return "FAIL"

def main():
    update_market_index()
    universe_map = get_universe()
    if not universe_map: return
    ranks_data = load_json_safe(RESULT_FILE, {'data': []})
    rank_map = {item['ticker']: item['final_score'] for item in ranks_data.get('data', [])}
    health_status = load_json_safe(HEALTH_STATUS_FILE, {})

    processed_count = 0
    max_calls_per_hour = 60
    
    # --- PHASE 1: High-Score Priority (Fast Track) ---
    print("\n--- Phase 1: High-Score Priority ---")
    high_score_tickers = [t for t, s in rank_map.items() if s > 0.9]
    for ticker in high_score_tickers:
        if processed_count >= max_calls_per_hour: break
        if ticker in universe_map and health_status.get(ticker, {}).get('status') != 'dead':
            res = update_stock(ticker, universe_map[ticker], score=rank_map[ticker], health_status=health_status)
            if res == "SUCCESS": processed_count += 1
            print(f"  {ticker}: {res}")

    # --- PHASE 2: Oldest Active Data (Unified Queue) ---
    print("\n--- Phase 2: Updating Oldest Active Stocks ---")
    update_candidates = []
    for ticker in universe_map:
        if health_status.get(ticker, {}).get('status') == 'dead':
            continue
            
        file_path = os.path.join(DATA_DIR, f"{ticker}.json")
        if not os.path.exists(file_path):
            update_candidates.append({'ticker': ticker, 'updated_at': '1900-01-01T00:00:00', 'score': rank_map.get(ticker, 0)})
        else:
            data = load_json_safe(file_path, {})
            updated_at = data.get('updated_at', '1900-01-01T00:00:00')
            score = rank_map.get(ticker, 0)
            ttl_hours = TTL_HIGH_SCORE if score > 0.9 else (TTL_MID_SCORE if score > 0.6 else TTL_LOW_SCORE)
            if (datetime.now() - datetime.fromisoformat(updated_at)).total_seconds() / 3600 > ttl_hours:
                update_candidates.append({'ticker': ticker, 'updated_at': updated_at, 'score': score})

    update_candidates.sort(key=lambda x: x['updated_at'])
    print(f"  Found {len(update_candidates)} active stocks needing update. Budget remaining: {max_calls_per_hour - processed_count}")
    
    for candidate in update_candidates:
        if processed_count >= max_calls_per_hour:
            print("\n⏸️  Rate limit reached. Stopping.")
            break
        ticker = candidate['ticker']
        res = update_stock(ticker, universe_map[ticker], score=candidate['score'], health_status=health_status)
        if res == "SUCCESS": processed_count += 1
        print(f"  Updating {ticker} [Age: {candidate['updated_at']}]: {res}")
        if processed_count % 5 == 0: time.sleep(6)

    # --- PHASE 3: Recovery Phase (Dead Stocks) ---
    # We only attempt to recover a very small number of dead stocks per cycle to save API budget.
    print("\n--- Phase 3: Recovery Phase (Dead Stocks) ---")
    dead_stocks = [t for t, info in health_status.items() if info.get('status') == 'dead']
    if dead_stocks:
        # Shuffle to ensure different dead stocks get a chance over time
        random.shuffle(dead_stocks)
        # Limit recovery to 5 stocks per hour
        recovery_limit = 5
        recovery_batch = dead_stocks[:recovery_limit]
        print(f"  Attempting to recover {len(recovery_batch)} dead stocks...")
        
        for ticker in recovery_batch:
            if processed_count >= max_calls_per_hour: break
            if ticker in universe_map:
                res = update_stock(ticker, universe_map[ticker], score=rank_map.get(ticker, 0), health_status=health_status)
                if res == "SUCCESS": 
                    processed_count += 1
                    print(f"  ✨ {ticker}: Recovered! (Status -> active)")
                else:
                    print(f"  ❌ {ticker}: Still dead ({res})")
                time.sleep(2)

    save_json(HEALTH_STATUS_FILE, health_status)
    print(f"\n✅ Cycle complete. Total successful updates: {processed_count}")

if __name__ == "__main__":
    main()
