import os, json, yfinance as yf
from datetime import datetime

DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
TICKERS = ["2330", "5498", "6223", "6291"]

def force_update():
    for ticker in TICKERS:
        file_path = os.path.join(DATA_DIR, f"{ticker}.json")
        if not os.path.exists(file_path):
            continue
            
        with open(file_path, 'r') as f:
            data = json.load(f)
            
        print(f"Updating {ticker}...")
        try:
            yf_ticker = yf.Ticker(f"{ticker}.TW")
            info = yf_ticker.info
            
            # Update prices (using yfinance history to be sure)
            hist = yf_ticker.history(period="1d")
            if not hist.empty:
                data['prices'] = [{
                    'date': hist.index[0].strftime('%Y-%m-%d'),
                    'close': float(hist['Close'].iloc[0]),
                    'open': float(hist['Open'].iloc[0]),
                    'high': float(hist['High'].iloc[0]),
                    'low': float(hist['Low'].iloc[0]),
                    'volume': int(hist['Volume'].iloc[0])
                }]
            
            # Update financials
            data['roe'] = info.get('trailinglares_roe', 0)
            data['pb'] = info.get('priceToBook', 0)
            data['pe'] = info.get('trailingPE', 0)
            yield_val = info.get('dividendYield', 0)
            data['yield'] = round(yield_val * 100, 2) if isinstance(yield_val, float) else 0
            
            data['updated_at'] = datetime.now().isoformat()
            
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"✅ {ticker} updated successfully.")
        except Exception as e:
            print(f"❌ {ticker} failed: {e}")

if __name__ == "__main__":
    force_update()
