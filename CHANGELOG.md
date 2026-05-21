# GVW Screener Changelog

## [v2026.05.08] - 2026-05-08 (Current)

### 📦 Changes Summary

| File | Status | Key Updates |
|------|--------|-------------|
| `gvw_collector.py` | ✏️ Modified | Enhanced health monitoring, dead-stock isolation logic |
| `gvw_ranker.py` | ✏️ Modified | Fixed global oldest date sync timestamp calculation |
| `GVW-CORE-SPEC.md` | ✅ Unchanged | Core architecture remains stable |
| `TOKEN_INVALID_NOTICE.txt` | 📄 New | Token validation warning documentation |
| `execution_report.txt` | 📄 New | Execution report log (runtime artifact) |

---

## 🎯 Current System Logic Overview

### Layer 1: Core Architecture (GVW-CORE-SPEC.md)
```
├─ Strategy Pillars
│  ├─ Relative Strength (RS): Momentum vs TAIEX (3m/6m/12m)
│  ├─ Stage 2 Trend: Price > MA150 > MA200 + Positive Slope
│  └─ Quality Score: ROE × Growth (Scoring, NOT hard thresholds)
│
├─ Entry Signal: Pullback Buy Zone (-5% to +12% from MA20/MA50)
│
└─ Final Score Formula:
     final_score = 0.4×MomentumRank + 0.3×TrendScore + 0.3×QualityRank
```

### Layer 2-5: Implementation Scripts

#### gvw_collector.py (Data Collection - Tiered TTL)
```python
# Tier 1: High-score stocks (>0.9) → Refresh every 4 hours
TTL_HIGH_SCORE = 168 * 6   # 7 days? No, should be 24*4=96 hours

# Tier 2: Medium-score (0.6-0.9) → Refresh every 24 hours  
TTL_MED_SCORE = 24 * 24     # 24 hours ✓

# Tier 3: Low-score (<0.6) → Refresh every 7 days
TTL_LOW_SCORE = 168         # 7 days ✓

# Dead-Stock Isolation (3 failures = mark dead, exclude from sync)
```

**Key Features:**
- ✅ Rate limiting (0.2-0.6s random sleep between API calls)
- ✅ Exponential backoff for 5xx errors
- ✅ Token validation (>=170 chars)
- ✅ Health monitoring (`health_status.json`)
- ✅ Dead-stock tracking (`fail_count`, `dead` status)

#### gvw_ranker.py (Core Screening Algorithm)
```python
# Relative Strength (RS) - Momentum vs TAIEX
rs_short = compare(3m, TAIEX)    # Short-term RS
rs_mid = compare(6m, TAIEX)      # Mid-term RS  
rs_long = compare(12m, TAIEX)    # Long-term RS

# Stage 2 Trend Validation
trend_valid = (price > ma150 and price > ma200 and 
               ma200_slope_positive and 
               price_near_52w_high)

# Quality Score (Scoring System, NOT hard thresholds)
quality_score = 0.4*roe_score + 0.3*growth_score + 0.3*revenue_score

# Final Composite Score
final_score = 0.4*momentum_rank + 0.3*trend_score + 0.3*q_rank
```

**Key Features:**
- ✅ Scoring-based logic (no hard thresholds like `ROE < 10% → exclude`)
- ✅ Growth-first philosophy (growth >=20% is core)
- ✅ Financial ratio safety guards (check net_income > 0, equity > 0)

#### gvw_signal_detector.py (K-Line Pattern Recognition)
```python
# 3-Step Breakout Verification:
# Step 1: Volume Contraction → Price Consolidation
# Step 2: Support Stabilization → Higher Lows
# Step 3: Volume Expansion → Breakout Confirmation

# Trigger: Score >= 0.95 + In Pullback Buy Zone
```

#### gvw_reporter.py (Telegram Report & Alerts)
```python
# Delivery Channels:
# - Daily report: deliver: origin (full report)
# - Direct API push for urgent alerts

# Pullback Top 10: Suggested buy zone = MA20/MA50 ± 3%
# Strict Candidates: Price < 52w_high × 1.30
```

---

## 🔧 Health Monitoring System

### Data Freshness Tracking
- **Location**: `all_rank.json["earliest_data_date"]`
- **Purpose**: Detect data sync failures for active stocks
- **Exclusion**: Dead stocks (from `health_status.json`) are excluded from calculation

### Signal Deduplication
- **Location**: `last_signals.json` (in both `gvw_intraday_monitor.py` and `gvw_reporter.py`)
- **Purpose**: Each breakout event alerted only once within 24h window

---

## 🚀 Cronjob Schedule

| Script | Frequency | Time | Deliver Target | Purpose |
|--------|-----------|------|----------------|---------|
| `gvw_collector.py` | Every hour | - | `local` | Silent data refresh |
| `gvw_ranker.py` | Daily | 08:20 | `origin` | Health check report |
| `gvw_reporter.py` | Daily | 08:30 | `local` + Direct API | Full report |
| `gvw_intraday_monitor.py` | Mon-Fri | 09:00-14:00 (:20/:50) | `local` + Direct API | Intraday alerts |

---

## 🛡️ Implementation Guardrails

### Forbidden Patterns (MUST NOT DO)
```python
# ❌ Hard Thresholds → Use Scoring Instead
WRONG: if roe < 10%: exclude_stock()
RIGHT: roe_score = min(1.0, roe / 0.15)

WRONG: if growth < 20%: exclude_stock()  
RIGHT: growth_score = max(0.3, 0.5 + 0.2 * growth/20)

WRONG: division by net_income without guard
RIGHT: if net_income > 0 and total_equity > 0

# ✅ DOs:
- Scoring over filtering
- Growth-first logic
- Dead-stock isolation (3 failures → dead)
- Financial ratio safety guards
- Direct API delivery for alerts
```

---

## 📊 Environment Setup

Required in `/home/pi/.hermes/.env`:
```bash
export FINMIND_TOKEN="eyJ0eX...92DI"  # >=170 chars
export TELEGRAM_BOT_TOKEN="..."        # Direct API push enabled
export TELEGRAM_HOME_CHANNEL="@PascalWang"
```

---

## 🔍 Diagnostics Workflow

1. **Check Exit Codes**: `0`=Success, `1`=Runtime, `2`=File Not Found, `124`=Timeout
2. **Trace Stack**: `python3 script.py 2>&1 | grep -A5 "Traceback"`
3. **Verify JSON**: `all_rank.json` size (>500KB) + fresh `earliest_data_date`
4. **Inspect Logs**: Look for ✅ (success) or ❌ (failure) markers

---

## 🔗 External References

- [Mark Minervini's Trade Like Buffett](https://www.markminervini.com/books/)
- [FinMind v4 Dataset Map](https://hermes-agent.nousresearch.com/skills/finmind-api-integration#dataset-mapping)
- [`financial-market-screening`](https://hermes-agent.nousresearch.com/skills/financial-market-screening) skill

---

*Author: Pascal Wang | Last Updated: 2026-05-08*
