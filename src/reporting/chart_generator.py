"""Headless dark-theme trade setup chart (matplotlib Agg), returned as PNG bytes."""
import io
import logging
from typing import Any, Dict, Optional

from src.analysis.trade_geometry import compute_trade_plan, normalize_bars

logger = logging.getLogger(__name__)

BG = "#131722"
UP = "#ef5350"    # Taiwan convention: red up
DOWN = "#26a69a"  # green down
CJK_FONTS = [
    "PingFang TC", "Heiti TC", "Noto Sans CJK TC", "Noto Sans TC",
    "Microsoft JhengHei", "WenQuanYi Zen Hei", "Arial Unicode MS", "DejaVu Sans",
]
MAX_BARS = 120


def render_trade_chart(
    ticker: str,
    name: str,
    prices: Any,
    plan: Optional[Dict[str, Any]] = None,
    market: str = "TWSE",
) -> Optional[bytes]:
    """Render the chart. Returns None if data is insufficient or matplotlib is missing."""
    bars = normalize_bars(prices)
    plan = plan if plan is not None else compute_trade_plan(prices)
    if plan is None or len(bars) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed; trade chart skipped.")
        return None

    offset = max(0, len(bars) - MAX_BARS)
    view = bars[offset:]
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=130)
    try:
        plt.rcParams["font.sans-serif"] = CJK_FONTS
        plt.rcParams["axes.unicode_minus"] = False
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)

        for x, b in enumerate(view):
            color = UP if b["close"] >= b["open"] else DOWN
            ax.vlines(x, b["low"], b["high"], color=color, linewidth=0.8)
            lo, hi = sorted((b["open"], b["close"]))
            ax.bar(x, max(hi - lo, 1e-6), bottom=lo, width=0.6, color=color)

        closes = [b["close"] for b in view]
        for window, color in ((5, "#f5a623"), (20, "#448aff"), (60, "#b266ff")):
            pts = [
                (x, sum(closes[x - window + 1:x + 1]) / window)
                for x in range(window - 1, len(closes))
            ]
            if pts:
                ax.plot(*zip(*pts), color=color, linewidth=1.1)

        last = len(view) - 1
        right = last + max(8, len(view) // 6)
        ax.set_xlim(-1, right)
        ax.axhspan(plan["box_low"], plan["box_high"], color="#7e57c2", alpha=0.12)

        def badge(y: float, label: str, face: str, style: str = "-", line: str = None):
            ax.axhline(y, color=line or face, linestyle=style, linewidth=1.2, alpha=0.9)
            ax.text(
                last + 1, y, f" {label} {y:.1f} ", color="white", va="center", ha="left",
                fontsize=9, fontweight="bold",
                bbox=dict(boxstyle="square,pad=0.3", facecolor=face, edgecolor="none"),
            )

        badge(plan["target_2"], "目標二", "#e5535d", "--")
        badge(plan["target_1"], "目標一", "#e5535d", "--")
        badge(plan["entry"], "進場", "#3d7bff")
        badge(plan["stop_loss"], "停損", "#2fb37a")
        ax.text(0, plan["box_high"], f"平台上緣 {plan['box_high']:.1f}", color="#9aa4b2",
                va="bottom", fontsize=9)
        ax.text(0, plan["box_low"], f"平台下緣 {plan['box_low']:.1f}", color="#9aa4b2",
                va="bottom", fontsize=9)

        span = plan["target_2"] - plan["stop_loss"]
        for ann in plan["annotations"]:
            x = ann["index"] - offset
            if x < 0 or x > last:
                continue
            ax.annotate(
                ann["text"], xy=(x, ann["price"]),
                xytext=(x, ann["price"] - span * 0.08),
                ha="center", va="top", color="#ffb74d", fontsize=9, fontweight="bold",
                arrowprops=dict(arrowstyle="-|>", color="#ffb74d", linewidth=1),
            )

        latest, prev = view[-1], view[-2]
        pct = (latest["close"] / prev["close"] - 1) * 100 if prev["close"] else 0.0
        fig.text(0.07, 0.94, f"{ticker} {name}({market})", color="white",
                 fontsize=14, fontweight="bold", ha="left")
        fig.text(0.93, 0.94, f"{latest['close']:.1f} ({pct:+.2f}%)  {latest['date']}",
                 color=UP if pct >= 0 else DOWN, fontsize=11, ha="right")

        ax.set_ylim(
            min(plan["stop_loss"], min(b["low"] for b in view)) - span * 0.15,
            max(plan["target_2"], max(b["high"] for b in view)) + span * 0.05,
        )
        ax.yaxis.tick_left()
        ax.tick_params(colors="#9aa4b2", labelsize=8)
        ax.set_xticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.grid(axis="y", color="#2a2e39", linewidth=0.6)
        fig.subplots_adjust(left=0.07, right=0.93, top=0.9, bottom=0.04)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        return buf.getvalue()
    finally:
        plt.close(fig)

