import logging
from typing import Dict, Any, Optional
import pandas as pd
from src.state.interfaces import IStateRepository
from src.state.state_manager import StateManager
from src.analysis.signals import get_strong_buy_signals

logger = logging.getLogger(__name__)

def get_suggested_price(ticker: str, state_manager: Optional[IStateRepository] = None) -> str:
    """Calculate suggested buy range based on MA20/MA50 from the stock's data."""
    state = state_manager or StateManager()
    data = state.get_stock_data(ticker)
    prices = data.get('prices', [])
    if not prices:
        return "N/A"

    try:
        df = pd.DataFrame(prices)
        if 'close' not in df.columns:
            return "N/A"
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        if len(df) < 50:
            return "N/A"

        sma20 = df['close'].rolling(window=20).mean().iloc[-1]
        sma50 = df['close'].rolling(window=50).mean().iloc[-1]

        low = min(sma20, sma50)
        high = max(sma20, sma50) * 1.03
        return f"{low:.1f} ~ {high:.1f}"
    except Exception:
        return "N/A"

def get_financials(ticker: str, state_manager: Optional[IStateRepository] = None) -> Dict[str, Any]:
    """Fetch financial indicators directly from the stock's state, falling back to ValuationService if missing."""
    state = state_manager or StateManager()
    data = state.get_stock_data(ticker)
    basic_info = data.get('basic_info', {})
    financials = data.get('financials', {})

    # Extract ROE
    pl = financials.get('income_statement', {})
    bs = financials.get('balance_sheet', {})
    net_income = float(pl.get('net_income', 0))
    equity = float(bs.get('total_equity', 0))
    roe = (net_income / equity * 100.0) if equity > 0 else float(basic_info.get('roe', 0))

    pb = float(basic_info.get('pb', 0) or basic_info.get('price_to_book', 0))
    pe = float(basic_info.get('pe', 0) or basic_info.get('trailing_pe', 0))
    dy = float(basic_info.get('yield', 0) or basic_info.get('dividend_yield', 0))
    is_loss = bool(basic_info.get('is_loss'))

    if pb == 0 and dy == 0:
        try:
            from src.providers.valuation_service import get_valuation_service
            val = get_valuation_service().get_valuation(ticker)
            if val:
                pb = val['pb']
                dy = val['yield']
                pe = val['pe']
                is_loss = val.get('is_loss', False)
        except Exception as exc:
            logger.debug("Could not fallback valuation in get_financials for %s: %s", ticker, exc)

    return {
        "roe": round(roe, 1),
        "pb": round(pb, 1),
        "pe": round(pe, 1),
        "yield": round(dy, 1),
        "is_loss": is_loss or (pb > 0 and pe <= 0),
    }

def generate_daily_report(state_manager: Optional[IStateRepository] = None) -> str:
    """Generate the full daily markdown/HTML report."""
    state = state_manager or StateManager()

    # Load rank data and identify the oldest source date used in these results.
    report_data = state.get_ranks()
    all_data = report_data.get('data', [])
    data_dates = [row.get('price_data_as_of') for row in all_data]
    data_cutoff = (
        min(data_dates)
        if data_dates and all(isinstance(date, str) and date for date in data_dates)
        else '未知'
    )

    if not all_data:
        return "📅 <b>資料截止至</b>: <code>未知</code>\n目前沒有可用的排名資料。\n"

    # 3. Strong Buy Signals (K-Line Verified)
    strong_signals = get_strong_buy_signals(state_manager=state)
    lines = [
        f"📅 <b>資料截止至</b>: <code>{data_cutoff}</code>\n",
        "🚨 <b>強烈買入訊號 (K-line Verified)</b>\n"
    ]

    if not strong_signals:
        lines.append("目前無符合 K 線突破之標的，請耐心等待\n")
    else:
        for i, sig in enumerate(strong_signals[:10], 1):
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
            name = row.get('name', ticker)
            score = row.get('final_score', 0)

            price_range = get_suggested_price(ticker, state_manager=state)
            fin = get_financials(ticker, state_manager=state)

            pe_val = fin['pe']
            pe_str = f"{pe_val}" if pe_val > 0 else ("N/A(虧損)" if fin.get('is_loss') or fin['pb'] > 0 else "0.0")
            financials_str = f"ROE: {fin['roe']}% | P/B: {fin['pb']} | P/E: {pe_str} | 股利: {fin['yield']}%"
            lines.append(f"{i}. {name} ({ticker}) - 得分: {score:.2f} | 區間: {price_range}\n")
            lines.append(f"   📊 財務: <code>{financials_str}</code>\n")

    # 5. GVW Strict Candidates
    gvw_strict = [row for row in all_data if row.get('is_gvw_strict')]
    gvw_strict_sorted = sorted(
        gvw_strict, key=lambda x: x.get('final_score', 0), reverse=True
    )[:10]

    lines.append("\n💎 <b>GVW 嚴格對齊名單 (Strict)</b>\n")
    if not gvw_strict_sorted:
        lines.append("目前無完全符合 GVW 嚴格標準之標的\n")
    else:
        for i, row in enumerate(gvw_strict_sorted, 1):
            t = row['ticker']
            price_range = get_suggested_price(t, state_manager=state)
            lines.append(f"{i}. {row.get('name', t)} ({t}) | 區間: {price_range}\n")

    return "".join(lines)
