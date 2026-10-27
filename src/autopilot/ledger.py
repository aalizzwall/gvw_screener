"""Recommendation ledger: records daily picks, measures realized forward returns,
and derives (never auto-applies) score-weight proposals from the evidence."""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from src.analysis.ranker import DEFAULT_SCORE_WEIGHTS, load_score_weights
from src.autopilot.benchmark import BenchmarkSeries
from src.config import DATA_DIR

logger = logging.getLogger("gvw.autopilot.ledger")

HORIZONS = (1, 5, 20)
KEEP_DAYS = 120
FACTOR_COLUMNS = {"momentum": "m_rank", "trend": "t_score", "quality": "q_rank"}


def format_performance_section(summary: Dict[str, Any]) -> str:
    """Telegram-HTML block of realized recommendation performance; '' when nothing is measured yet."""
    measured = [(h, v) for h, v in summary.get("horizons", {}).items() if v["n"]]
    if not measured:
        return ""
    lines = ["\n📈 <b>歷史推薦績效（Top 50 實測）</b>\n"]
    for h, v in measured:
        line = f"• T+{h}：勝率 {v['win_rate']:.0%}｜平均 {v['avg_return']:+.2%}"
        if v.get("n_excess"):
            line += f"｜相對大盤 {v['avg_excess']:+.2%}（贏大盤 {v['beat_rate']:.0%}）"
        lines.append(line + f"｜n={v['n']}\n")
    return "".join(lines)


class RecommendationLedger:
    def __init__(self, state: Any, path: Optional[str] = None):
        self.state = state
        self.path = path or os.path.join(DATA_DIR, "recommendations.json")

    # ---- persistence ----
    def _load(self) -> List[Dict[str, Any]]:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.warning(f"Ledger unreadable, starting fresh: {e}")
        return []

    def _save(self, entries: List[Dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries[-KEEP_DAYS:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---- price helpers ----
    def _price_series(self, ticker: str) -> pd.DataFrame:
        data = self.state.get_stock_data(ticker) or {}
        df = pd.DataFrame(data.get("prices", []))
        if df.empty or "close" not in df.columns or "date" not in df.columns:
            return pd.DataFrame(columns=["date", "close"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        return df.dropna(subset=["close", "date"]).sort_values("date").reset_index(drop=True)

    # ---- recording ----
    def record(self, ranks: Dict[str, Any], top_n: int = 50, as_of: Optional[str] = None) -> int:
        """Snapshot today's top picks (idempotent per date). Returns number of picks stored."""
        rows = (ranks or {}).get("data", [])[:top_n]
        if not rows:
            return 0
        day = as_of or datetime.now().strftime("%Y-%m-%d")
        picks = []
        for row in rows:
            series = self._price_series(row["ticker"])
            eligible = series[series["date"] <= day]
            if eligible.empty:
                continue
            picks.append({
                "ticker": row["ticker"],
                "name": row.get("name", ""),
                "final_score": float(row.get("final_score", 0)),
                "entry_signal": row.get("entry_signal", ""),
                "m_rank": float(row.get("m_rank", 0)),
                "t_score": float(row.get("t_score", 0)),
                "q_rank": float(row.get("q_rank", 0)),
                "base_date": eligible["date"].iloc[-1],
                "base_close": float(eligible["close"].iloc[-1]),
                "outcomes": {},
            })
        entries = [e for e in self._load() if e.get("date") != day]
        entries.append({"date": day, "picks": picks})
        entries.sort(key=lambda e: e["date"])
        self._save(entries)
        return len(picks)

    # ---- evaluation ----
    def evaluate(self, benchmark: Optional[BenchmarkSeries] = None) -> int:
        """Fill in forward returns (and excess vs TAIEX when available). Returns outcomes newly filled."""
        entries = self._load()
        cache: Dict[str, pd.DataFrame] = {}
        filled = 0
        changed = False
        for entry in entries:
            for pick in entry.get("picks", []):
                excess = pick.setdefault("excess", {})
                pending = [
                    h for h in HORIZONS
                    if str(h) not in pick["outcomes"] or (benchmark and str(h) not in excess)
                ]
                if not pending:
                    continue
                series = cache.setdefault(pick["ticker"], self._price_series(pick["ticker"]))
                idx_list = series.index[series["date"] == pick["base_date"]].tolist()
                if not idx_list or pick["base_close"] <= 0:
                    continue
                idx = idx_list[0]
                for h in pending:
                    if idx + h >= len(series):
                        continue
                    if str(h) not in pick["outcomes"]:
                        ret = float(series["close"].iloc[idx + h]) / pick["base_close"] - 1
                        pick["outcomes"][str(h)] = round(ret, 5)
                        filled += 1
                        changed = True
                    if benchmark and str(h) not in excess:
                        bench = benchmark.ret(pick["base_date"], series["date"].iloc[idx + h])
                        if bench is not None:
                            excess[str(h)] = round(pick["outcomes"][str(h)] - bench, 5)
                            changed = True
        if changed:
            self._save(entries)
        return filled

    # ---- reporting ----
    def summary(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"horizons": {}, "by_signal": {}}
        picks = [p for e in self._load() for p in e.get("picks", [])]
        for h in HORIZONS:
            rets = [p["outcomes"][str(h)] for p in picks if str(h) in p["outcomes"]]
            exc = [p["excess"][str(h)] for p in picks if str(h) in p.get("excess", {})]
            out["horizons"][h] = {
                "n": len(rets),
                "win_rate": round(sum(r > 0 for r in rets) / len(rets), 4) if rets else None,
                "avg_return": round(sum(rets) / len(rets), 5) if rets else None,
                "n_excess": len(exc),
                "avg_excess": round(sum(exc) / len(exc), 5) if exc else None,
                "beat_rate": round(sum(x > 0 for x in exc) / len(exc), 4) if exc else None,
            }
        by_signal: Dict[str, List[float]] = {}
        for p in picks:
            if "5" in p["outcomes"]:
                by_signal.setdefault(p.get("entry_signal") or "none", []).append(p["outcomes"]["5"])
        for sig, rets in by_signal.items():
            out["by_signal"][sig] = {
                "n": len(rets),
                "win_rate": round(sum(r > 0 for r in rets) / len(rets), 4),
                "avg_return": round(sum(rets) / len(rets), 5),
            }
        return out

    # ---- tuning proposal (never auto-applied) ----
    def propose_weights(self, min_samples: int = 150) -> Optional[Dict[str, Any]]:
        rows = [
            {**{col: p[col] for col in FACTOR_COLUMNS.values()},
             "ret": p.get("excess", {}).get("5", p["outcomes"]["5"])}  # prefer market-neutral return
            for e in self._load() for p in e.get("picks", []) if "5" in p["outcomes"]
        ]
        if len(rows) < min_samples:
            return None
        df = pd.DataFrame(rows)
        # Spearman = Pearson on ranks (avoids a scipy dependency on the Pi)
        ret_rank = df["ret"].rank()
        corr = {name: float(df[col].rank().corr(ret_rank)) for name, col in FACTOR_COLUMNS.items()}
        if any(pd.isna(v) for v in corr.values()):
            return None
        current = load_score_weights()
        raw = {k: max(v, 0.0) + 0.05 for k, v in corr.items()}
        total = sum(raw.values())
        # Blend 50/50 with current weights, clamp, renormalise: damp overfitting to a short sample.
        blended = {k: 0.5 * current[k] + 0.5 * raw[k] / total for k in DEFAULT_SCORE_WEIGHTS}
        clamped = {k: min(max(v, 0.15), 0.6) for k, v in blended.items()}
        norm = sum(clamped.values())
        proposed = {k: round(v / norm, 3) for k, v in clamped.items()}
        if max(abs(proposed[k] - current[k]) for k in proposed) < 0.03:
            return None
        return {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "samples": len(rows),
            "horizon": 5,
            "correlation": {k: round(v, 4) for k, v in corr.items()},
            "current": {k: round(v, 3) for k, v in current.items()},
            "proposed": proposed,
        }
