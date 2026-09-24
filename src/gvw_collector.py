import os, json, sys, pandas as pd, time, random, math, traceback
from datetime import datetime, timedelta
import requests
import yfinance as yf

ENDPOINT_MAP = {
    "top": "/exchangeReport/MI_INDEX",           # Market Index / Overall Stats
    "financials_pl": "/opendata/t187ap06_L_ci",  # Income Statement (General) - For ROE
    "financials_bs": "/opendata/t187ap07_L_ci",  # Balance Sheet (General)
    "daily_all": "/exchangeReport/STOCK_DAY_ALL",
    "pe_pb": "/exchangeReport/BWIBBU_ALL",      # P/E and P/B Ratios
    "basic_info": "/opendata/t187ap03_L",
    "financials_pl_o": "/opendata/t187ap14_L",   # Income Statement for OTC (if needed later)
}

DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
UNIVERSE_FILE = '/home/pi/.hermes/gvw_screener/universe.json'
HEALTH_STATUS_FILE = '/home/pi/.hermes/gvw_screener/health_status.json'
MARKET_INDEX_FILE = '/home/pi/.hermes/gvw_screener/market_index.json'
LOG_FILE = '/home/pi/.hermes/gvw_screener/logs/collector.log'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'

TTL_HIGH_SCORE = 4
TTL_MID_SCORE = 24
TTL_LOW_SCORE = 168

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0"
]

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(formatted_msg + '\n')
            f.flush()
    except: pass

def fetch_prices_yfinance(ticker):
    symbol = f"{ticker}.TW"
    try:
        df = yf.Ticker(symbol).history(period="1y", interval="1d")
        if df is None or df.empty: return None
        return [{'date': date.strftime('%Y-%m-%d'), 'close': float(row['Close'])} for date, row in df.iterrows()]
    except: return None

def get_twse_data(dataset_key, params=None):
    path = ENDPOINT_MAP.get(dataset_key)
    if not path: 
        log(f"[TRACK: TWSE] ❌ Dataset key '{dataset_key}' not found in mapping.")
        return None
    url = f"https://openapi.twse.com.tw/v1{path}"
    headers = {'User-Agent': random.choice(USER_AGENTS)}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        if response.status_code == 200: 
            data = response.json()
            if data:
                log(f"[TRACK: TWSE] ✅ Successfully fetched {dataset_key} data from {path}.")
                return data
            else:
                log(f"[TRACK: TWSE] ⚠️ {dataset_key} returned empty data.")
                return None
        elif response.status_code == 429:
            log("[TRACK: TWSE] ⚠️ Rate limited. Retrying after 60s...")
            time.sleep(60)
            response = requests.get(url, params=params, headers=headers, timeout=30)
            if response.status_code == 200:
                log(f"[TRACK: TWSE] ✅ Retry successful!")
                return response.json()
            else:
                log(f"[TRACK: TWSE] ❌ Still failed with HTTP {response.status_code}")
                return None
        else:
            log(f"[TRACK: TWSE] ❌ HTTP {response.status_code} - API unavailable")
            return None
    except Exception as e:
        log(f"[TRACK: TWSE] ❌ Unexpected error: {e}")
        return None

def load_json_safe(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f: return json.load(f)
        except: pass
    return default

def save_json(path, data):
    with open(path, 'w') as f: json.dump(data, f, indent=2)

def update_stock(ticker, name, score=None, health_status=None, force=False):
    file_path = os.path.join(DATA_DIR, f"{ticker}.json")
    existing_data = load_json_safe(file_path, {})
    if not health_status:
        health = {'status': 'active', 'fail_count': 0}
    else:
        health = health_status.get(ticker, {'status': 'active', 'fail_count': 0, 'updated_at': '1900-01-01T00:00:00'})
    
    # DEAD = permanently skip
    if health.get('status') == 'dead': return "SKIP"
    
    ttl_hours = TTL_HIGH_SCORE if (score or 0) > 0.9 else (TTL_MID_SCORE if (score or 0) > 0.6 else TTL_LOW_SCORE)
    updated_at = existing_data.get('updated_at', '1900-01-01T00:00:00')
    
    # Retry status: allow exactly one retry attempt, always refresh timestamp
    if health.get('status') == 'retry':
        fail_count = health.get('fail_count', 0)
        # If already failed once, this is the retry attempt - mark as active or dead
        if fail_count >= 1:
            health['fail_count'] = fail_count + 1
            health['status'] = 'active' if fail_count == 1 else 'dead'
        # Refresh timestamp per memory: "updated_at is refreshed for every attempt"
        existing_data['updated_at'] = datetime.now().isoformat()
    
    # Skip if retry exhausted or TTL passed (unless force)
    if health.get('status') == 'dead': return "SKIP"
    if not force and (datetime.fromisoformat(updated_at) + timedelta(hours=ttl_hours) >= datetime.now()): 
        return "SKIP"
    
    # Fetch prices - if it fails, mark as retry
    new_prices = fetch_prices_yfinance(ticker)
    if not new_prices:
        health['fail_count'] = 1
        health['status'] = 'retry'
        # Refresh timestamp per memory even on retry mark
        existing_data['updated_at'] = datetime.now().isoformat()
        return "retry"
    
    existing_data['prices'] = new_prices
    existing_data['updated_at'] = datetime.now().isoformat()
    
    # Fetch financial data (P/E, P/B, ROE, Dividend Yield)
    pe_pb_data = get_twse_data("pe_pb", params={"ticker": ticker})
    financials_pl_data = get_twse_data("financials_pl", params={"ticker": ticker})
    
    pb_ratio = 0.0
    pe_ratio = 0.0
    roe = 0.0
    dividend_yield = 0.0

    if pe_pb_data and isinstance(pe_pb_data, list):
        for item in pe_pb_data:
            if item.get('Code') == ticker:
                pb_ratio = float(item.get('PBratio', 0) or 0)
                pe_ratio = float(item.get('PEratio', 0) or 0)
                dividend_yield = float(item.get('DividendYield', 0) or 0)
                break

    if financials_pl_data and isinstance(financials_pl_data, list):
        for item in financials_pl_data:
            if item.get('公司代號') == ticker:
                # Basic EPS as proxy for ROE logic if specific ROE not in this endpoint
                roe = float(item.get('基本每股盈餘（元）', 0) or 0) 
                break

    existing_data['name'] = name
    existing_data['pb_ratio'] = pb_ratio
    existing_data['pe_ratio'] = pe_ratio
    existing_data['roe'] = roe
    existing_data['dividend_yield'] = dividend_yield
    
    health[ticker] = {'fail_count': 0, 'status': 'active'}
    
    # Sync health back to health_status (only if not None)
    if health_status is not None:
        health_status[ticker] = health
    
    existing_data['updated_at'] = datetime.now().isoformat()
    save_json(file_path, existing_data)
    return "SUCCESS"

def main():
    try:
        universe_map = load_json_safe(UNIVERSE_FILE, {})
        rank_map = {item['ticker']: item['final_score'] for item in load_json_safe(RESULT_FILE, {'data': []}).get('data', [])}
        health_status = load_json_safe(HEALTH_STATUS_FILE, {})

        basic_info_data = get_twse_data("basic_info")
        ticker_to_name_map = {item.get('公司代號', item.get('代號')): item.get('公司簡稱', item.get('名稱', 'Unknown')) for item in basic_info_data} if basic_info_data else {}
        
        processed_tickers = set()
        
        tiered_stocks = {'high': [], 'mid': [], 'low': []}
        for t in universe_map:
            score = rank_map.get(t, 0)
            if not score: continue
            tier = 'high' if score > 0.9 else ('mid' if score > 0.6 else 'low')
            tiered_stocks[tier].append(t)
            
        target_batch = []
        for tier, threshold in {'high': 0.1, 'mid': 0.15, 'low': 0.25}.items():
            target_batch.extend(tiered_stocks[tier][:math.ceil(len(tiered_stocks[tier]) * threshold)])
        
        for ticker in target_batch:
            stock_name = ticker_to_name_map.get(ticker, 'Unknown')
            res = update_stock(ticker, stock_name, score=rank_map.get(ticker, 0), health_status=health_status, force=True)
            
            # Handle result and ensure health_status entry exists
            if ticker not in health_status:
                health_status[ticker] = {'status': 'active', 'fail_count': 0, 'updated_at': datetime.now().isoformat()}
            
            # Only count actual success as processed
            if res == "SUCCESS": processed_tickers.add(ticker)
            # Retry stocks need another attempt - update health_status in place
            if res == "retry":
                # Refresh updated_at for the stock even on retry mark (memory requirement)
                health_status[ticker]['updated_at'] = datetime.now().isoformat()
            
        save_json(HEALTH_STATUS_FILE, health_status)
        log(f"Cycle complete. Processed: {len(processed_tickers)}/{len(universe_map)} stocks.")
    except Exception as e:
        log(f"CRITICAL: {e}")

if __name__ == "__main__":
    main()
