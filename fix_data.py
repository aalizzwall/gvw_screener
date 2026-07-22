import os
import json
import requests
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RANK_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'

def get_twse(endpoint):
    logging.info(f"Fetching data from {endpoint}...")
    try:
        r = requests.get(f'https://openapi.twse.com.tw/v1{endpoint}', timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.error(f"Error fetching {endpoint}: {e}")
    return []

def main():
    # Get actual raw data from API
    info_all = get_twse('/opendata/t187ap03_L') or []
    sh_all = get_twse('/opendata/t187ap02_L') or []
    
    # Map Ticker -> Raw Industry Code (NO MAPPING TO TEXT YET)
    info_map = {}
    for i in info_all:
        if isinstance(i, dict):
            raw_ticker = i.get('公司代號')
            if raw_ticker:
                clean_ticker = str(raw_ticker).strip()
                code = str(i.get('產業別', '未知')).strip()
                info_map[clean_ticker] = code
    
    sh_map = {}
    for s in sh_all:
        if isinstance(s, dict):
            raw_ticker = s.get('公司代號')
            if raw_ticker:
                clean_ticker = str(raw_ticker).strip()
                try:
                    val_str = s.get('持股比率', '0').replace('%', '').strip()
                    val = float(val_str) if val_str else 0.0
                    sh_map[clean_ticker] = sh_map.get(clean_ticker, 0.0) + val
                except:
                    pass

    try:
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            ranks = json.load(f).get('data', [])
    except Exception as e:
        logging.error(f"Failed to read rank file: {e}")
        return

    fixed = 0
    for r in ranks:
        ticker = str(r['ticker']).strip()
        file_path = os.path.join(DATA_DIR, f"{ticker}.json")
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                updated = False
                # REVERT to raw code to avoid wrong mapping
                industry = info_map.get(ticker, '未知')
                if data.get('industry') != industry:
                    data['industry'] = industry
                    updated = True
                
                concentration = sh_map.get(ticker, 0.0)
                if data.get('concentration') != concentration:
                    data['concentration'] = concentration
                    updated = True
                
                if updated:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False)
                    fixed += 1
            except Exception as e:
                logging.debug(f"Skip {ticker}: {e}")

    logging.info(f"Finished! Reverted {fixed} stocks to RAW industry codes to eliminate errors.")

if __name__ == "__main__":
    main()
