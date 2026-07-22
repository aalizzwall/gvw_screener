import os, json, time
from datetime import datetime
import requests
import yfinance as yf
import pandas as pd

DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
UNIVERSE_FILE = '/home/pi/.hermes/gvw_screener/universe.json'

def fetch_prices_yfinance(ticker):
    symbol = f"{ticker}.TW"
    try:
        df = yf.Ticker(symbol).history(period="1y", interval="1d")
        if df is None or df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return [{'date': date.strftime('%Y-%m-%d'), 'close': float(row['Close']),
                 'open': float(row['Open']), 'high': float(row['High']),
                 'low': float(row['Low']), 'volume': int(row['Volume'])}
                for date, row in df.iterrows()]
    except: return None

def main():
    if not os.path.exists(UNIVERSE_FILE):
        print("Universe file not found.")
        return
    with open(UNIVERSE_FILE, 'r') as f:
        universe = json.load(f)
    
    print(f"🚀 Starting FORCE FULL UPDATE for {len(universe)} stocks...")
    count = 0
    
    for ticker in universe:
        file_path = os.path.join(DATA_DIR, f"{ticker}.json")
        if not os.path.exists(file_path): continue
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Force update price
            new_prices = fetch_prices_yfinance(ticker)
            if new_prices:
                data['prices'] = new_prices
                data['updated_at'] = datetime.now().isoformat()
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                count += 1
                print(f"✅ {ticker} updated.")
            else:
                print(f"❌ {ticker} failed.")
            
            # Gentle throttle
            time.sleep(0.5)
            
        except Exception as e:
            print(f"⚠️ Error updating {ticker}: {e}")

    print(f"\n✨ Force update complete. Successfully updated {count}/{len(universe)} stocks.")

if __name__ == "__main__":
    main()
