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

# Load FINMIND token from environment variable (optional, API may work without auth)
FINMIND_TOKEN = os.getenv('FINMIND_TOKEN', '').strip()

print(f"✅ FINMIND_TOKEN loaded: {len(FINMIND_TOKEN) if FINMIND_TOKEN else 0} chars")

def get_finmind_data(dataset, params=None):
    """Standard FinMind v4 API wrapper with retry logic and rate limiting"""
    url = "https://api.finmindtrade.com/api/v4/data"
    
    # Prepare base query parameters
    query_params = {'dataset': dataset}
    
    # Add optional parameters if provided in params dict
    if params:
        for key, value in params.items():
            query_params[key] = value
    
    # Add default date range if not provided
    if 'start_date' not in query_params:
        query_params['start_date'] = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    if 'end_date' not in query_params:
        query_params['end_date'] = datetime.now().strftime('%Y-%m-%d')
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Prepare request headers - use Bearer token only if available and non-empty
            headers = {}
            if FINMIND_TOKEN and len(FINMIND_TOKEN) >= 170:
                headers['Authorization'] = f'Bearer {FINMIND_TOKEN}'
            
            print(f"[DEBUG] Attempting request for dataset={dataset}, params={query_params}, auth={bool(headers)}")
            response = requests.get(url, params=query_params, headers=headers, timeout=15)
            print(f"[DEBUG] Response status: {response.status_code}")
            if response.status_code == 402:
                print(f"\n❌ Rate limit exceeded (402). Stopping to avoid ban.")
                sys.exit(1)
            if response.status_code == 400 and 'msg' in response.json() and 'Token is illegal' in response.json().get('msg', ''):
                print(f"\n❌ Token illegal error (400). Skipping this attempt.")
                continue
            
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
        except Exception as e:
            print(f"[DEBUG] Request failed with error: {e}")
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
        # 嘗試獲取新數據，如果失敗則保留舊數據但重置 TTL
        new_prices = fetch_prices(ticker) if needs_price else None
        new_fins = fetch_financials(ticker) if needs_fin else None

        # 只要其中一個成功獲取，就更新對應欄位並刷新時間戳
        updated = False
        if new_prices:
            existing_data['prices'] = new_prices
            updated = True
        if new_fins:
            existing_data['financials'] = new_fins
            existing_data['fin_updated_at'] = datetime.now().isoformat()
            updated = True

        if updated:
            # 刷新主時間戳，讓標的回到隊列末端
            existing_data['updated_at'] = datetime.now().isoformat()
            existing_data['ticker'] = ticker
            existing_data['name'] = name
            save_json(file_path, existing_data)
            if health_status is not None:
                health_status[ticker] = {'fail_count': 0, 'status': 'active'}
            print("✅")
            return "SUCCESS"
        else:
            # 如果兩項都失敗，重置 TTL 讓下次能重新輪詢
            if needs_price or needs_fin:
                existing_data['updated_at'] = datetime.now().isoformat()
                existing_data['fin_updated_at'] = datetime.now().isoformat()
                save_json(file_path, existing_data)
                print("⏱️  (TTL reset)")
                # 不增加 fail_count，避免變成 dead stock
            return "SKIP"

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

    # --- TIER 3: Regular Scan (Process ALL expired stocks) ---
    print("\n--- Phase 3: Process Expired Stocks ---")

    # Gather all existing stocks and their last update times
    stock_ages = []
    for ticker in universe_list:
        file_path = os.path.join(DATA_DIR, f"{ticker}.json")
        if os.path.exists(file_path):
            data = load_json_safe(file_path, {})
            updated_at = data.get('updated_at', '1900-01-01T00:00:00')
            # 計算相對年齡 (小時)
            current_age_hours = (datetime.now() - datetime.fromisoformat(updated_at)).total_seconds() / 3600
            stock_ages.append((ticker, updated_at, current_age_hours))

    # Sort by updated_at ascending (oldest first)
    stock_ages.sort(key=lambda x: x[2])

    # Process ALL expired stocks (not just 50 oldest), with rate limiting
    batch = [t for t, _, age in stock_ages if age > TTL_HIGH_SCORE]  # 只處理真的過期的標的
    
    for ticker in batch:
        update_stock(ticker, universe_map[ticker], score=rank_map.get(ticker, 0), health_status=health_status)

    save_json(HEALTH_STATUS_FILE, health_status)
    print(f"\nCycle complete. Updated top {len(batch)} oldest stocks.")

if __name__ == "__main__":
    main()
