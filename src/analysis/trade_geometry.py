"""Trade geometry: consolidation platform, entry/stop/targets and pattern anchors.

Pure-Python on purpose (no pandas) so it is cheap and easy to unit test.
Output is a deterministic reference plan, not investment advice.
"""
from typing import Any, Dict, List, Optional

MIN_BARS = 30
PLATFORM_WINDOW = 40
RECENT_BARS = 5
MAX_BOX_WIDTH = 0.35  # box_high/box_low - 1; wider is not a platform
ENTRY_BUFFER = 1.003
STOP_BUFFER = 0.995
TARGET1_R = 1.5
TARGET2_R = 3.0


def _num(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def normalize_bars(prices: Any) -> List[Dict[str, Any]]:
    """Return bars with date/open/high/low/close/volume; drop unusable rows."""
    bars: List[Dict[str, Any]] = []
    if not isinstance(prices, list):
        return bars
    for row in prices:
        if not isinstance(row, dict):
            continue
        close = _num(row.get("close"))
        if close is None or close <= 0:
            continue
        vol = _num(row.get("volume", row.get("Trading_Volume", row.get("vol"))))
        high = _num(row.get("high", row.get("max")))
        low = _num(row.get("low", row.get("min")))
        open_ = _num(row.get("open"))
        bars.append({
            "date": str(row.get("date", "")),
            "open": open_ if open_ else close,
            "high": max(high if high else close, close),
            "low": min(low if low else close, close),
            "close": close,
            "volume": vol if vol is not None else 0.0,
        })
    return bars


def _percentile(values: List[float], pct: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def compute_trade_plan(prices: Any) -> Optional[Dict[str, Any]]:
    """Derive platform, entry, stop, targets and annotations from daily bars.

    Returns None when data is insufficient or no plausible platform exists.
    """
    bars = normalize_bars(prices)
    if len(bars) < MIN_BARS:
        return None

    n = len(bars)
    plat_end = n - RECENT_BARS  # platform formed before the most recent bars
    plat_start = max(0, plat_end - PLATFORM_WINDOW)
    window = bars[plat_start:plat_end]
    if len(window) < 15:
        return None

    box_high = _percentile([b["high"] for b in window], 0.95)
    box_low = _percentile([b["low"] for b in window], 0.05)
    if box_low <= 0 or box_high <= box_low:
        return None
    if box_high / box_low - 1 > MAX_BOX_WIDTH:
        return None

    entry = round(box_high * ENTRY_BUFFER, 2)
    stop = round(box_low * STOP_BUFFER, 2)
    risk = entry - stop
    if risk <= 0:
        return None
    target_1 = round(entry + TARGET1_R * risk, 2)
    target_2 = round(entry + TARGET2_R * risk, 2)

    annotations: List[Dict[str, Any]] = []

    # 末量線低: lowest-volume bar inside the platform
    vol_bars = [(i, b) for i, b in enumerate(window, start=plat_start) if b["volume"] > 0]
    if vol_bars:
        i, b = min(vol_bars, key=lambda t: t[1]["volume"])
        annotations.append({"index": i, "price": b["low"], "text": "末量線低", "kind": "volume_low"})

    # 破底: lowest low in platform that closed back above box_low
    i, b = min(enumerate(window, start=plat_start), key=lambda t: t[1]["low"])
    if b["low"] < box_low and b["close"] >= box_low:
        annotations.append({"index": i, "price": b["low"], "text": "破底", "kind": "spring"})

    # 突破: recent bar closing above box_high with volume > 1.5x 20d average
    avg_vol_20 = sum(x["volume"] for x in bars[max(0, plat_end - 20):plat_end]) / max(
        1, min(20, plat_end)
    )
    breakout_idx = None
    for i in range(plat_end, n):
        b = bars[i]
        if b["close"] > box_high and avg_vol_20 > 0 and b["volume"] > 1.5 * avg_vol_20:
            breakout_idx = i
            break
    if breakout_idx is not None:
        annotations.append({
            "index": breakout_idx,
            "price": bars[breakout_idx]["low"],
            "text": "突破",
            "kind": "breakout",
        })

    return {
        "box_high": round(box_high, 2),
        "box_low": round(box_low, 2),
        "entry": entry,
        "stop_loss": stop,
        "target_1": target_1,
        "target_2": target_2,
        "risk": round(risk, 2),
        "breakout_confirmed": breakout_idx is not None,
        "annotations": annotations,
    }

