import os
import json
import pandas as pd
from datetime import datetime
from gvw_signal_detector import get_strong_buy_signals

# ================= CONFIGURATION =================
DATA_DIR = '/home/pi/.hermes/gvw_screener/data'
RESULT_FILE = '/home/pi/.hermes/gvw_screener/all_rank.json'
MARKET_INDEX_FILE = '/home/pi/.hermes/gvw_screener/market_index.json'

def get_suggested_price(ticker):
    """Calculate suggested buy range based on MA20/MA50 from the stock's JSON file"""
    file_path = os.path.join(DATA_DIR, f"{ticker}.json")
    if not os.path.exists(file_path):
        return "N/A"
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        prices = data.get('prices', [])
        if not prices: return "N/A"
        
        df = pd.DataFrame(prices)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        if len(df) < 50: return "N/A"
        
        sma20 = df['close'].rolling(window=20).mean().iloc[-1]
        sma50 = df['close'].rolling(window=50).mean().iloc[-1]
        
        low = min(sma20, sma50)
        high = max(sma20, sma50) * 1.03
        return f"{low:.1f} ~ {high:.1f}"
    except Exception:
        return "N/A"

def get_financials(ticker):
    """Fetch financial indicators directly from the stock's JSON file"""
    file_path = os.path.join(DATA_DIR, f"{ticker}.json")
    if not os.path.exists(file_path):
        return {"roe": 0, "pb": 0, "pe": 0, "yield": 0}
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        return {
            "roe": data.get('roe', 0),
            "pb": data.get('pb', 0),
            "pe": data.get('pe', 0),
            "yield": data.get('yield', 0)
        }
    except Exception:
        return {"roe": 0, "pb": 0, "pe": 0, "yield": 0}

def generate_daily_report():
    """Generate the full daily report with correct financial data"""
    # 1. System Heartbeat
    sync_health = "未知"
    if os.path.exists(MARKET_INDEX_FILE):
        try:
            with open(MARKET_INDEX_FILE, 'r') as f:
                m_data = json.load(f)
                sync_health = m_data.get('updated_at', '未知')
        except: pass

    # 2. Load Rank Data
    if not os.path.exists(RESULT_FILE):
        return "❌ 錯誤：找不到排名數據文件 (all_rank.json)"
    
    try:
        with open(RESULT_FILE, 'r') as f:
            report_data = json.load(f)
        all_data = report_data.get('data', []) if isinstance(report_data, dict) else report_data
    except Exception as e:
        return f"❌ 讀取數據失敗: {e}"

    if not all_data:
        return "📡 系統同步正常，但目前篩選名單為空。"

    # 3. Strong Buy Signals (K-Line)
    strong_signals = get_strong_buy_signals()
    lines = [f"📡 <b>系統最後同步時間</b>: <code>{sync_health}</code>\n", "🚨 <b>強烈買入訊號 (K-line Verified)</b>\n"]
    
    if not strong_signals:
        lines.append("目前無符合 K 線突破之標的，請耐心等待\n")
    else:
        for i, sig in enumerate(strong_signals, 1):
            lines.append(f"{i}. {sig['name']} ({sig['ticker']}) - 得分: {sig['score']:.2f} | 狀態: 突破訊號確認!\n")

    # 4. Pullback Top 10
    pullbacks = [row for row in all_data if row.get('entry_signal') == '🟢 回調買入區']
    pullbacks_sorted = sorted(pullbacks, key=lambda x: x.get('final_score', 0), reverse=True)
    top_10 = pullbacks_sorted[:10]
    
    lines.append("\n🎯 <b>回調買入 Top 10 (建議價格)</b>\n")
    if not top_10:
        lines.append("目前無符合回調條件之標的\n")
    else:
        for i, row in enumerate(top_10, 1):
            ticker = row['ticker']
            name = row['name']
            score = row.get('final_score', 0)
            
            # CORRECTED: Fetch from JSON, not from all_rank.json row
            price_range = get_suggested_price(ticker)
            fin = get_financials(ticker)
            
            financials_str = f"ROE: {fin['roe']:.1f}% | P/B: {fin['pb']:.1f} | P/E: {fin['pe']:.1f} | 股利: {fin['yield']}%"
            lines.append(f"{i}. {name} ({ticker}) - 得分: {score:.2f} | 區間: {price_range}\n")
            lines.append(f"   📊 財務: <code>{financials_str}</code>\n")

    # 5. GVW Strict Candidates
    gvw_strict = [row for row in all_data if row.get('is_gvw_strict')]
    gvw_strict_sorted = sorted(gvw_strict, key=lambda x: x.get('final_score', 0), reverse=True)
    
    lines.append("\n💎 <b>GVW 嚴格對齊名單 (Strict)</b>\n")
    if not gvw_strict_sorted:
        lines.append("目前無完全符合 GVW 嚴格標準之標的\n")
    else:
        for i, row in enumerate(gvw_strict_sorted, 1):
            price_range = get_suggested_price(row['ticker'])
            lines.append(f"{i}. {row['name']} ({row['ticker']}) | 區間: {price_range}\n")

    return "".join(lines)
