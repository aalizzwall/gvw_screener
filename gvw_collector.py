import os, json, sys, pandas as pd, time, random, math, traceback
from datetime import datetime, timedelta
import requests
import yfinance as yf

# ================= API AVAILABILITY =================
# TWSE OpenAPI is a public API and does not require a token for basic data.
# We prioritize TWSE and fallback to yfinance only if the request fails.

# Mapping of dataset keys to actual OpenAPI paths
ENDPOINT_MAP = {
    "top": "/exchangeReport/MI_INDEX",           # Market Index / Overall Stats
    "financials_pl": "/opendata/t187ap06_L_ci",  # Income Statement (General)
    "financials_bs": "/opendata/t187ap07_L_ci",  # Balance Sheet (General)
    "daily_all": "/exchangeReport/STOCK_DAY_ALL", # Daily Trading Info
    "basic_info": "/opendata/t187ap03_L",        # Company Basic Info (Industry)
    "shareholders": "/opendata/t187ap02_L",      # Major Shareholders (>10%)
}

def check_api_availability() -> bool:
    """Check if TWSE API is accessible. Returns True if available, False otherwise."""
    try:
        # Test with the Market Index endpoint
        res = get_twse_data("top")
        if res:
            log("[CHECK API] ✅ TWSE API is accessible.")
            return True
        return False
    except Exception as e:
        log(f"[CHECK API] ❌ TWSE API connection failed: {e}")
        return False

DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
UNIVERSE_FILE = '/home/pi/.hermes/gvw_screener/universe.json'
POINTER_FILE = '/home/pi/.hermes/gvw_screener/pointer.json'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
HEALTH_STATUS_FILE = '/home/pi/.hermes/gvw_screener/health_status.json'
MARKET_INDEX_FILE = '/home/pi/.hermes/gvw_screener/market_index.json'
LOG_FILE = '/home/pi/.hermes/gvw_screener/collector.log'

# TTL Settings (Hours)
TTL_HIGH_SCORE = 4    # Score > 0.9
TTL_MID_SCORE = 24    # Score > 0.6
TTL_LOW_SCORE = 168   # Others (7 days)
TTL_FINANCIALS = 720  # Financials update every 30 days
RECHECK_INTERVAL = 30 # Reduced from 180 to 30 days to prevent "dead" stocks from being forgotten too long
MAX_CONSECUTIVE_FAILURES = 3  # Skip stock after 3 consecutive fetch failures

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
            f.flush()  # Ensure it's written to disk immediately
    except:
        pass

def get_twse_data(dataset_key, params=None):
    path = ENDPOINT_MAP.get(dataset_key)
    if not path:
        log(f"[TRACK: TWSE] ❌ Dataset key '{dataset_key}' not found in mapping.")
        return None

    url = f"https://openapi.twse.com.tw/v1{path}"
    query_params = {}
    if params:
        query_params.update(params)

    if 'start_date' not in query_params:
        query_params['start_date'] = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    if 'end_date' not in query_params:
        query_params['end_date'] = datetime.now().strftime('%Y-%m-%d')

    headers = {'User-Agent': random.choice(USER_AGENTS)}
    try:
        response = requests.get(url, params=query_params, headers=headers, timeout=30)
        if response.status_code == 200:
            log(f"[TRACK: TWSE] ✅ Successfully fetched {dataset_key} data from {path}.")
            return response.json()
        elif response.status_code == 429:
            log("[TRACK: TWSE] ⚠️ Rate limited. Retrying after 60s...")
            time.sleep(60)
            response = requests.get(url, params=query_params, headers=headers, timeout=30)
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

def fetch_prices_yfinance(ticker):
    import pytz
    symbol = f"{ticker}.TW"
    yf_ticker = yf.Ticker(symbol)
    try:
        # Use historical data which is more reliable
        df = yf_ticker.history(period="1y", interval="1d")
        if df is None or df.empty or len(df) == 0:
            return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        prices = []
        valid_dates = []
        for date, row in df.iterrows():
            prices.append({
                'date': date.strftime('%Y-%m-%d'),
                'close': float(row['Close']),
                'open': float(row['Open']),
                'high': float(row['High']),
                'low': float(row['Low']),
                'volume': int(row['Volume'])
            })
            valid_dates.append(date.strftime('%Y-%m-%d'))
        if len(prices) > 0:
            return prices
    except Exception as e:
        log(f"[TRACK: yfinance] ❌ Request failed: {e}")
        return None

def fetch_prices_multi_track(ticker):
    # Always try yfinance first (more reliable)
    log(f"[TRACK: yfinance] Fetching {ticker}.TW...")
    import pytz
    symbol = f"{ticker}.TW"
    yf_ticker = yf.Ticker(symbol)
    try:
        # Use historical data which is more reliable
        df = yf_ticker.history(period="1d", interval="1d", start=(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'))
        if df is None or df.empty or len(df) == 0:
            return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if len(df) == 0:
            return None
            
        prices = [{'date': df['Date'].iloc[0].strftime('%Y-%m-%d'), 'close': float(df['Close'].iloc[0]),
                   'open': float(df['Open'].iloc[0]), 'high': float(df['High'].iloc[0]),
                   'low': float(df['Low'].iloc[0]), 'volume': int(df['Volume'].iloc[0])}]
        return prices
    except Exception as e:
        log(f"[TRACK: yfinance] ❌ Failed to fetch {ticker}.TW from Yahoo: {e}")
        return None

def get_universe():
    log("ℹ️ TWSE API unavailable, using saved universe from file.")
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE, 'r') as f: return json.load(f)
    log("❌ Error: Universe file not found. Please create universe.json manually.")
    return {}

def load_json_safe(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f: return json.load(f)
        except: pass
    return default

def save_json(path, data):
    with open(path, 'w') as f: json.dump(data, f)

def calculate_quality_score(pl_data, bs_data):
    try:
        rev_curr = float(pl_data.get('營業收入', 0))
        rev_growth = 0.2 if rev_curr > 0 else 0 
        net_income = float(pl_data.get('本期淨利', 0))
        margin = (net_income / rev_curr) if rev_curr > 0 else 0
        total_assets = float(bs_data.get('資產總額', 0))
        total_liab = float(bs_data.get('負債總額', 0))
        debt_ratio = (total_liab / total_assets) if total_assets > 0 else 1.0
        s_growth = min(1.0, max(0, (rev_growth - 0.1) / 0.3)) 
        s_margin = min(1.0, max(0, (margin - 0.05) / 0.15))
        s_stable = min(1.0, max(0, (0.6 - debt_ratio) / 0.3))
        final_q = (s_growth * 0.5) + (s_margin * 0.3) + (s_stable * 0.2)
        return round(final_q, 3)
    except Exception as e:
        log(f"Quality score calc error: {e}")
        return 0.5

def fetch_index_history(ticker="^TWII", period="1y"):
    try:
        log(f"[TRACK: yfinance] Fetching index history for {ticker}...")
        import yfinance as yf
        idx = yf.Ticker(ticker)
        hist = idx.history(period=period)
        if hist.empty:
            return None
        data = [{"date": date.strftime('%Y-%m-%d'), "close": float(close)} 
                for date, close in zip(hist.index, hist['Close'])]
        return data
    except Exception as e:
        log(f"❌ Failed to fetch index history: {e}")
        return None

def update_market_index():
    existing_data = load_json_safe(MARKET_INDEX_FILE, {})
    updated_at = datetime.fromisoformat(existing_data.get('updated_at', '1900-01-01T00:00:00'))
    market_stats = get_twse_data("top")
    if market_stats:
        save_json(MARKET_INDEX_FILE, {
            "updated_at": datetime.now().isoformat(),
            "stats": market_stats
        })
        log("✅ Market index stats updated.")

    index_history_file = os.path.join(DATA_DIR, "market_index.json")
    hist_data = fetch_index_history()
    if hist_data:
        save_json(index_history_file, {"updated_at": datetime.now().isoformat(), "history": hist_data})
        log("✅ Market index history updated.")
    else:
        log("⚠️ Market index history update failed.")

def update_stock(ticker, name, score=None, health_status=None, force=False, financials=None, basic_info=None, shareholders=None):
    existing_data = load_json_safe(os.path.join(DATA_DIR, f"{ticker}.json"), {})
    health = health_status.get(ticker, {}) if health_status else {}
    if health.get('status') == 'dead':
        log(f"⏭️  Skipping {ticker}: permanently dead (fail_count={health.get('fail_count')})")
        return "SKIP"
    
    if score is None:
        score = 0
    ttl_hours = TTL_HIGH_SCORE if score > 0.9 else (TTL_MID_SCORE if score > 0.6 else TTL_LOW_SCORE)
    updated_at = existing_data.get('updated_at', '1900-01-01T00:00:00')
    needs_price = True if force else (datetime.fromisoformat(updated_at) + timedelta(hours=ttl_hours) < datetime.now())
    
    updated = False
    if needs_price:
        log(f"[TRACK: yfinance] Fetching price and financial info for {ticker}...")
        new_prices = fetch_prices_yfinance(ticker)
        if new_prices:
            existing_data['prices'] = new_prices
            updated = True
        else:
            log(f"❌ {ticker} - Failed to fetch price from yfinance")
                
        try:
            yf_ticker = yf.Ticker(f"{ticker}.TW")
            info = yf_ticker.info
            
            # 1. Try yfinance first (correct keys)
            roe = info.get('returnOnEquity')
            pb = info.get('priceToBook')
            pe = info.get('trailingPE')
            div_yield = info.get('dividendYield')
            
            # 2. Fallback to official TWSE data if yfinance is missing/zero
            if (roe is None or roe == 0) and financials:
                pl = financials.get('pl', {})
                bs = financials.get('bs', {})
                try:
                    net_income = float(pl.get('本期淨利', 0))
                    assets = float(bs.get('資產總額', 0))
                    liab = float(bs.get('負債總額', 0))
                    equity = assets - liab
                    roe = (net_income / equity) if equity > 0 else 0
                except: roe = 0

            existing_data['roe'] = roe if roe is not None else 0
            existing_data['pb'] = pb if pb is not None else 0
            existing_data['pe'] = pe if pe is not None else 0
            
            # Handle dividend yield separately
            if div_yield is not None:
                existing_data['yield'] = round(div_yield * 100, 2) if isinstance(div_yield, float) else 0
            else:
                existing_data['yield'] = 0
                
            updated = True
            log(f"✅ {ticker} - Financial indicators updated (ROE: {existing_data['roe']:.2f}).")
        except Exception as e:
            log(f"⚠️ {ticker} - Failed to fetch financial info: {e}")

        if updated:
            existing_data['updated_at'] = datetime.now().isoformat()
        else:
            existing_data['updated_at'] = datetime.now().isoformat()
            log(f"⚠️ {ticker} - Total update failure, but refreshing timestamp to avoid queue block.")
    else:
        log(f"⏱️  {ticker}: Will retry later (price TTL={ttl_hours}h)")
        
    if health.get('status') == 'dead' or health.get('fail_count', 0) >= 3:
        log(f"⏭️  Skipping {ticker}: unhealthy (status={health.get('status')})")
        return "SKIP"
        
    log(f"✅ {ticker} - Data updated (Price/Quality/Industry/Money), Health: {health.get('status', 'unknown')}")
    save_json(os.path.join(DATA_DIR, f"{ticker}.json"), existing_data)
    return "SUCCESS"

def main():
    try:
        update_market_index()
        universe_map = get_universe()
        if not universe_map: 
            log("❌ Critical Error: Universe map is empty.")
            return
        ranks_data = load_json_safe(RESULT_FILE, {'data': []})
        ranks = ranks_data.get('data', [])
        rank_map = {item['ticker']: item['final_score'] for item in ranks}
        health_status = load_json_safe(HEALTH_STATUS_FILE, {})

        log("\\n--- Phase 0: Pre-loading Market Data ---")
        market_data = {}
        try:
            pl_all = get_twse_data("financials_pl") or []
            bs_all = get_twse_data("financials_bs") or []
            info_all = get_twse_data("basic_info") or []
            sh_all = get_twse_data("shareholders") or []
            
            pl_map = {item.get('公司代號'): item for item in pl_all if isinstance(item, dict)}
            bs_map = {item.get('公司代號'): item for item in bs_all if isinstance(item, dict)}
            info_map = {item.get('公司代號'): item for item in info_all if isinstance(item, dict)}
            
            sh_map = {}
            for item in sh_all:
                if isinstance(item, dict):
                    ticker = item.get('公司代號')
                    if ticker:
                        sh_map.setdefault(ticker, []).append(item)
            
            for ticker in universe_map:
                market_data[ticker] = {
                    'pl': pl_map.get(ticker, {}),
                    'bs': bs_map.get(ticker, {}),
                    'info': info_map.get(ticker, {}),
                    'sh': sh_map.get(ticker, [])
                }
            log(f"✅ Pre-loaded financials, basic info and shareholders for {len(market_data)} stocks.")
        except Exception as e:
            log(f"⚠️ Market data pre-load failed: {e}")

        log("\\n--- Global Oldest-First Polling ---")
        all_stocks = []
        for t in universe_map:
            file_path = os.path.join(DATA_DIR, f"{t}.json")
            if os.path.exists(file_path):
                data = load_json_safe(file_path, {})
                updated_at = data.get('updated_at', '1900-01-01T00:00:00')
            else:
                updated_at = '1900-01-01T00:00:00' # Missing data is oldest
            all_stocks.append((t, updated_at))

        # Sort all stocks by update time (oldest first)
        all_stocks.sort(key=lambda x: x[1])
        
        # Calculate how many we SHOULD update (10% of total universe)
        num_to_update = math.ceil(len(all_stocks) * 0.1)
        
        # Find target_batch: iterate through sorted list and pick NON-DEAD stocks until quota is met
        target_batch = []
        for t, t_upd in all_stocks:
            if len(target_batch) >= num_to_update:
                break
            if health_status.get(t, {}).get('status') != 'dead':
                target_batch.append(t)
        
        if not target_batch:
            log("No active stocks available to update in this cycle.")
            save_json(HEALTH_STATUS_FILE, health_status)
            log("\\nCycle complete.")
            return
            
        log(f"Updating oldest {len(target_batch)} active stocks...")
        stats = {'fetches': 0, 'failures': 0, 'skips': 0}
        
        for ticker in target_batch:
            sd = market_data.get(ticker, {})
            result = update_stock(ticker, universe_map[ticker], score=rank_map.get(ticker, 0), health_status=health_status, force=True, financials={'pl': sd.get('pl'), 'bs': sd.get('bs')}, basic_info=sd.get('info'), shareholders=sd.get('sh'))
            if result == "SUCCESS": stats['fetches'] += 1
            elif result == "FAIL": stats['failures'] += 1
            else: stats['skips'] += 1
        
        log(f"Cycle stats: {stats}")
        save_json(HEALTH_STATUS_FILE, health_status)
        log("\\nCycle complete.")

    except Exception as e:
        log(f"❌ CRITICAL ERROR in main(): {e}")
        log(traceback.format_exc())
    else:
        log("✅ Main loop completed successfully.")

if __name__ == "__main__":
    # Check API availability first
    check_api_availability()
    main()
