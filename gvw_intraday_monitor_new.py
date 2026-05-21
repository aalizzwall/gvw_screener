"""
gvw_intraday_monitor.py - 盤中即時監控系統 (API 驅動模式)

功能說明:
- 每 10 分鐘掃描一次符合條件的候選標的 (score >= 0.95 + 回調買入區)
- 透過 FinMind API 抓取今日 K 線數據進行驗證
- 執行 K 線三步法：量能萎縮 → 支撐企穩 → 帶量突破
- 發送 Telegram 即時警報

⚠️ API 負載控制:
- Phase 3 Limit: 最大 60 筆/小時 (global_calls)
- FinMind 限額：600 次/小時
- 當前預估負載：~156 次/小時 (26 候選標的 × 每 10 分鐘)
"""

import os, json, sys, time, random
from datetime import datetime, timedelta
import requests
import subprocess
import pandas as pd

# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
SIGNAL_CACHE_FILE = '/home/pi/.hermes/gvw_screener/last_signals.json'
CALLS_FILE = '/home/pi/.hermes/gvw_screener/api_calls_count.json'

# API 負載控制
PHASE3_MAX_CALLS_PER_HOUR = 60

# FinMind Token (可從環境變數讀取)
FINMIND_TOKEN = os.getenv('FINMIND_TOKEN', '').strip()

# 監控頻率 (秒)
INTERVAL_SECONDS = 600


def load_json_safe(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f: return json.load(f)
    except: pass
    return default


def save_json(path, data):
    with open(path, 'w') as f: json.dump(data, f)


def check_api_quota():
    """檢查 API 配額是否足夠"""
    api_calls = load_json_safe(CALLS_FILE, {'count': 0, 'hour_start': ''})
    hour_start = api_calls.get('hour_start', datetime.now().isoformat())
    count = api_calls.get('count', 0)
    
    now = datetime.now()
    if not hour_start or str(now.strftime('%Y-%m-%d %H')) != str(datetime.fromisoformat(hour_start).strftime('%Y-%m-%d %H')):
        print(f"\n⏰ [NEW HOUR] Resetting API counter...")
        hour_start = now.strftime('%Y-%m-%d %H')
        count = 0
    
    api_calls['hour_start'] = hour_start
    api_calls['count'] = count
    save_json(CALLS_FILE, api_calls)
    
    return count


def record_api_call():
    """記錄一次 API 呼叫"""
    api_calls = load_json_safe(CALLS_FILE, {'count': 0, 'hour_start': ''})
    api_calls['count'] += 1
    
    now = datetime.now()
    if str(now.strftime('%Y-%m-%d %H')) != api_calls.get('hour_start', ''):
        api_calls['count'] = 0
        api_calls['hour_start'] = now.strftime('%Y-%m-%d %H')
    
    save_json(CALLS_FILE, api_calls)
    print(f"📊 [API USAGE] Hour: {api_calls['hour_start']} | Count: {api_calls['count']}/{PHASE3_MAX_CALLS_PER_HOUR}")
    
    if api_calls['count'] >= PHASE3_MAX_CALLS_PER_HOUR:
        raise RateLimitError(f"今日 API 上限已達 ({PHASE3_MAX_CALLS_PER_HOUR}次/小時)")


class RateLimitError(Exception):
    pass


def get_finmind_kline(ticker, end_date=None):
    """從 FinMind v4 API 獲取 K 線數據"""
    url = "https://api.finmindtrade.com/api/v4/data"
    
    query_params = {
        'dataset': 'TAIWAN_HIS_KL',
        'stock_id': ticker,
        'start_date': (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d'),
        'end_date': end_date or datetime.now().strftime('%Y-%m-%d')
    }
    
    print(f"🔹 [API] Fetching kline for {ticker} | dataset={query_params['dataset']}")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            headers = {}
            if FINMIND_TOKEN and len(FINMIND_TOKEN) >= 170:
                headers['Authorization'] = f'Bearer {FINMIND_TOKEN}'
            
            response = requests.get(url, params=query_params, headers=headers, timeout=30)
            
            if response.status_code == 402:
                print(f"\n❌ [API] Rate limit exceeded (402). Please wait and try later.")
                sys.exit(1)
            
            if response.status_code == 400 and 'Token is illegal' in response.json().get('msg', ''):
                print(f"⚠️ [API] Token error, skipping attempt...")
                continue
            
            response.raise_for_status()
            res_json = response.json()
            
            data = res_json['data'] if isinstance(res_json, dict) and 'data' in res_json else res_json
            return data
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            if 500 <= status_code < 600:
                wait_time = (2 ** attempt) + random.random()
                print(f"⏳ [API] Server error, retrying in {wait_time:.1f}s...")
                time.sleep(wait_time)
            else:
                return None
        except Exception as e:
            print(f"⚠️ [API] Request failed with error: {e}")
            return None
    
    return None


def parse_kline_data(ticker, kline_data):
    """解析 K 線數據為 DataFrame"""
    try:
        df = pd.DataFrame(kline_data)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['vol'] = pd.to_numeric(df['Trading_Volume'], errors='coerce')
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        df = df.dropna(subset=['close', 'vol'])
        
        print(f"✅ [DATA] Parsed {len(df)} records for {ticker}")
        return df
        
    except Exception as e:
        print(f"❌ [DATA] Failed to parse kline data for {ticker}: {e}")
        return None


def check_kline_three_steps_api(ticker):
    """K 線三步法驗證 (API 驅動版)"""
    print(f"\n🔍 [CHECK] Validating {ticker} via API-driven kline analysis...")
    
    kline_data = get_finmind_kline(ticker)
    if not kline_data:
        print(f"⚠️ [CHECK] No kline data for {ticker}, skipping.")
        return None
    
    df = parse_kline_data(ticker, kline_data)
    if df is None:
        return None
    
    if len(df) < 21:
        print(f"⚠️ [CHECK] Insufficient data ({len(df)} < 21) for {ticker}, skipping.")
        return None
    
    # 1. Volume Dry-up
    avg_vol_20 = df['vol'].tail(20).mean()
    avg_vol_5 = df['vol'].tail(5).mean()
    vol_dry_up = avg_vol_5 <= (0.7 * avg_vol_20)
    
    if not vol_dry_up:
        print(f"  ✗ [VOL] Dry-up failed")
    
    # 2. Bottoming Out
    last_3_days = df['close'].tail(3)
    price_range = (last_3_days.max() - last_3_days.min()) / df['close'].iloc[-1]
    bottoming_out = price_range <= 0.03
    
    if not bottoming_out:
        print(f"  ✗ [BOTTOM] Range check failed")
    
    # 3. Breakout
    prev_5_max = df['close'].iloc[-6:-1].max()
    current_close = df['close'].iloc[-1]
    current_vol = df['vol'].iloc[-1]
    
    breakout_price = current_close > prev_5_max
    
    if not breakout_price:
        print(f"  ✗ [BREAKOUT] Price failed")
    
    breakout_vol = current_vol > (1.5 * avg_vol_20)
    
    if not breakout_vol:
        print(f"  ✗ [VOL BREAKOUT] Volume failed")
    
    all_passed = vol_dry_up and bottoming_out and (breakout_price and breakout_vol)
    
    print(f"  ✅ [RESULT] all_passed={all_passed}")
    
    return {
        "vol_dry_up": vol_dry_up,
        "bottoming_out": bottoming_out,
        "breakout": breakout_price and breakout_vol,
        "all_passed": all_passed
    }


def send_telegram_alert(ticker, name, score):
    """發送 Telegram 即時警報"""
    msg = (
        f"🚨 **GVW 盤中突破警報**\n\n"
        f"標的：{name} ({ticker})\n"
        f"強度得分：{score:.2f}\n"
        f"驗證狀態：✅ K 線三步法全通過！\n\n"
        f"⏰ [TIME] {datetime.now().strftime('%H:%M:%S')}\n"
        f"💡 建議立即檢查分時圖與掛單區間。"
    )
    
    try:
        cmd = ['hermes', 'chat', 'telegram', msg]
        subprocess.run(cmd, check=True)
        return True
    except Exception as e:
        print(f"❌ [ALERT] Error sending Telegram alert: {e}")
        return False


def main():
    """主執行流程"""
    
    if not os.path.exists(RESULT_FILE):
        print("⚠️ [START] No rank results found.")
        print("   Please run gvw_collector.py + gvw_ranker.py first.")
        return
    
    with open(RESULT_FILE, 'r') as f:
        report_data = json.load(f)
    
    all_data = report_data.get('data', []) if isinstance(report_data, dict) else report_data
    if not all_data:
        print("⚠️ [START] No ranking data found.")
        return
    
    candidates = [row for row in all_data 
                  if row['final_score'] >= 0.95 and row['entry_signal'] == '🟢 回調買入區']
    
    print(f"\n📋 [START] Found {len(candidates)} high-score pullback candidates:")
    for c in candidates[:10]:
        print(f"   {c['ticker']} ({c['name']}): score={float(c['final_score']):.2f}")
    
    if not candidates:
        print("⚠️ [START] No candidates found to monitor.")
        return
    
    last_signals = load_json_safe(SIGNAL_CACHE_FILE, {})
    
    new_signals_found = 0
    current_signals = {}
    
    print(f"\n🔍 [SCAN] Starting intraday scan for {len(candidates)} candidates...")
    print("-" * 50)
    
    try:
        initial_count = check_api_quota()
        print(f"📊 [API QUOTA] Hourly count: {initial_count}/{PHASE3_MAX_CALLS_PER_HOUR}")
        
        if initial_count >= PHASE3_MAX_CALLS_PER_HOUR:
            print("❌ [START] API quota exhausted for this hour. Exiting.")
            return
            
    except RateLimitError as e:
        print(f"❌ [START] {e}")
        return
    
    success_count = 0
    fail_count = 0
    
    for idx, row in enumerate(candidates):
        ticker = row['ticker']
        name = row['name']
        score = float(row['final_score'])
        
        print(f"\n{idx + 1}/{len(candidates)} | {ticker} | Score: {score:.2f}")
        
        res = check_kline_three_steps_api(ticker)
        
        if res is None:
            fail_count += 1
            continue
        
        if res['all_passed']:
            current_signals[ticker] = datetime.now().isoformat()
            
            last_alerted = last_signals.get(ticker)
            if not last_alerted:
                print(f"\n🔥 SIGNAL FOUND! {name} ({ticker}) | Score: {score:.2f}")
                if send_telegram_alert(ticker, name, score):
                    new_signals_found += 1
                success_count += 1
            else:
                print(f"  ℹ️ Still active signal (already alerted)")
        else:
            fail_count += 1
    
    record_api_call()
    
    updated_cache = {**last_signals, **current_signals}
    save_json(SIGNAL_CACHE_FILE, updated_cache)
    
    print("\n" + "=" * 50)
    print("📊 [SUMMARY]")
    print("=" * 50)
    print(f"總候選標的：{len(candidates)}")
    print(f"驗證成功：   {success_count}")
    print(f"驗證失敗：   {fail_count}")
    print(f"新訊號發送： {new_signals_found}")
    api_calls = load_json_safe(CALLS_FILE, {'count': 0})
    print(f"當前配額使用：{api_calls['count']}/{PHASE3_MAX_CALLS_PER_HOUR}/小時")
    print("=" * 50)
    
    if new_signals_found > 0:
        print("✅ [COMPLETE] Scan complete with alerts!")
    else:
        print("ℹ️ [COMPLETE] Scan complete, no new signals detected.")


if __name__ == "__main__":
    main()
