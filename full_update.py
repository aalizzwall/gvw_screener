import os, json, math, traceback
from datetime import datetime
from gvw_collector import (
    get_universe, load_json_safe, save_json, 
    get_twse_data, update_stock, LOG_FILE
)

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_msg = f"[{timestamp}] [FULL-UPDATE] {msg}"
    print(formatted_msg)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(formatted_msg + '\n')

def main():
    try:
        log("Starting Full Financial Data Update...")
        universe_map = get_universe()
        if not universe_map:
            log("❌ Universe map is empty.")
            return

        ranks_data = load_json_safe('/home/pi/.hermes/gvw_screener/all_rank.json', {'data': []})
        ranks = ranks_data.get('data', [])
        rank_map = {item['ticker']: item['final_score'] for item in ranks}
        health_status = load_json_safe('/home/pi/.hermes/gvw_screener/health_status.json', {})

        log("Pre-loading TWSE Market Data for all stocks...")
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
                if ticker: sh_map.setdefault(ticker, []).append(item)

        market_data = {}
        for ticker in universe_map:
            market_data[ticker] = {
                'pl': pl_map.get(ticker, {}),
                'bs': bs_map.get(ticker, {}),
                'info': info_map.get(ticker, {}),
                'sh': sh_map.get(ticker, [])
            }

        all_tickers = list(universe_map.keys())
        total = len(all_tickers)
        log(f"Processing {total} stocks...")

        success_count = 0
        for i, ticker in enumerate(all_tickers, 1):
            sd = market_data.get(ticker, {})
            # Force update all
            result = update_stock(
                ticker, 
                universe_map[ticker], 
                score=rank_map.get(ticker, 0), 
                health_status=health_status, 
                force=True, 
                financials={'pl': sd.get('pl'), 'bs': sd.get('bs')}, 
                basic_info=sd.get('info'), 
                shareholders=sd.get('sh')
            )
            if result == "SUCCESS":
                success_count += 1
            
            if i % 100 == 0:
                log(f"Progress: {i}/{total} ({success_count} successful)")

        log(f"Full update complete. Successfully updated {success_count}/{total} stocks.")
        save_json('/home/pi/.hermes/gvw_screener/health_status.json', health_status)

    except Exception as e:
        log(f"❌ CRITICAL ERROR: {e}")
        log(traceback.format_exc())

if __name__ == "__main__":
    main()
