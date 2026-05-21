# GVW Screener Release Notes

## Version 2026.05.09 (Current Branch)

### 🔥 CRITICAL FIX: API Rate Limit Protection

**Issue**: FinMind API returns `402 Rate Limit` when calling >600 times/hour.

**Root Cause Analysis**:
- Phase 3 processed **unlimited** expired stocks
- Combined with Phase 2's 50 missing stocks repair = 100+ calls/hour
- Consistently hitting FinMind's 600 calls/hour hard limit

**Solution Implemented**:
✅ Limit Phase 3 to max 60 calls/hour  
✅ Add rate pacing: 6s delay every 5 calls  
✅ Early exit with graceful message when limit reached  
✅ Next hour resumes from beginning of expired list

### 📦 What Changed

| Component | Status | Description |
|-----------|--------|-------------|
| `gvw_collector.py` | ✏️ **CRITICAL** | Added rate limiting to Phase 3 (max 60/hour + pacing) |
| `CHANGELOG.md` | ✏️ Updated | Added v2026.05.09 release notes |
| `RELEASE_NOTES.md` | ✏️ Updated | Updated with new rate limit protection info |

---

## Version 2026.05.08 (Previous Release)

---

### 🎯 System Architecture Logic

```
GVW Screener = 5 Layers (Core Spec → Strategy → Infrastructure → Operations → Reporting)
├─ Layer 1: Core Specification (GVW-CORE-SPEC.md)
│  └─ Defines strategy pillars, health metrics, implementation guardrails
│
├─ Layer 2: Strategy (`gvw_ranker.py`)
│  ├─ MomentumRank (40%): RS vs TAIEX over 3m/6m/12m windows
│  ├─ TrendScore (30%): Stage 2 alignment + Quality indicators
│  └─ QualityRank (30%): ROE × Growth with scoring system
│     └─ Formula: final_score = 0.4×MomentumRank + 0.3×TrendScore + 0.3×QualityRank
│
├─ Layer 3: Data Infrastructure (`gvw_collector.py`)
│  ├─ Tiered TTL Strategy:
│  │  ├─ High-score (>0.9): Refresh every 4h
│  │  ├─ Medium-score (0.6-0.9): Refresh every 24h
│  │  └─ Low-score (<0.6): Refresh every 7 days
│  ├─ Dead-stock Isolation: 3 failures → mark dead, exclude from sync
│  └─ Rate limiting + exponential backoff for API safety
│
├─ Layer 4: Operations
│  ├─ gvw_collector.py: Every hour → deliver: local (silent)
│  ├─ gvw_ranker.py: Daily 08:20 → deliver: origin (health check)
│  ├─ gvw_reporter.py: Daily 08:30 → deliver: local + Direct API push
│  └─ gvw_intraday_monitor.py: Mon-Fri 09:00-14:00 → Direct API alerts
│
└─ Layer 5: Reporting (`gvw_reporter.py`, `gvw_signal_detector.py`)
   ├─ Strong Buy Signals: K-line 3-step verified (Score ≥ 0.95)
   ├─ Pullback Top 10: Suggested buy zone = MA20/MA50 ± 3%
   └─ Strict Candidates: Price < 52w_high × 1.30

```

---

### 🔑 Key Logic Points

#### 1. **Scoring Over Filtering** ✅
```python
# ❌ WRONG - Never use hard thresholds:
WRONG: if roe < 10%: exclude_stock()

# ✅ CORRECT - Always use scoring:
CORRECT: roe_score = min(1.0, roe / 0.15)
growth_score = max(0.3, 0.5 + 0.2 * growth/20)
```

#### 2. **Growth-First Logic** ✅
```python
# Growth >= 20% is core philosophy
if growth < 0.2:
    if growth > -0.1:
        growth_score *= max(0.3, 0.5 + 0.2 * growth)
    else:
        growth_score *= 0.1  # Significant decline
else:
    growth_score *= 1.0  # Growth maintained
```

#### 3. **Dead-Stock Isolation** ✅
```python
# Track fail_count, mark as dead after 3 consecutive failures
count = health_status.get(ticker, {}).get('fail_count', 0) + 1
status = 'dead' if count >= 3 else 'active'

# Exclude dead stocks from sync calculation (via earliest_data_date)
sync_date = data.get('updated_at') if not is_dead else None
```

#### 4. **Tiered TTL Strategy** ✅
```python
TTL_HIGH_SCORE = 4    # High-score stocks: frequent refresh (keep fresh)
TTL_MID_SCORE = 24    # Medium-score: daily refresh
TTL_LOW_SCORE = 168   # Low-score: weekly refresh (save API calls)

# Dead stocks TTL reset to avoid false sync failure signals
```

#### 5. **Financial Ratio Safety** ✅
```python
# Always guard against negative denominators
if net_income > 0 and total_equity > 0:
    roe = current_ni / current_equity
```

---

### 📊 Data Freshness Monitoring

- **Location**: `all_rank.json["earliest_data_date"]`
- **Definition**: Minimum `updated_at` timestamp across all **active** stocks
- **Purpose**: Detect data sync failures for active stocks only
- **Exclusion Rule**: Dead stocks (from `health_status.json`) are excluded from calculation

---

### 🚀 Cronjob Schedule

| Script | Frequency | Time | Deliver Target | Purpose |
|--------|-----------|------|----------------|---------|
| Collector | Every hour | - | `local` | Silent data refresh |
| Ranker | Daily | 08:20 | `origin` | Health check report |
| Reporter | Daily | 08:30 | `local` + Direct API | Full report |
| Intraday Monitor | Mon-Fri | 09:00-14:00 (:20/:50) | Direct API | Real-time alerts |

---

### ⚠️ Critical Alert

**FINMIND TOKEN ISSUE**: Current token has expired and returns 400 "Token illegal" error.

**Resolution Steps:**
```bash
# 1. Get new token from https://financial.stanford.edu
# 2. Update /home/pi/.hermes/.env:
export FINMIND_TOKEN=<new_172_char_token>

# 3. Verify token length:
python3 -c "import os; t=os.getenv('FINMIND_TOKEN'); print(len(t))"
```

---

### 🔍 Diagnostics Commands

```bash
# Check exit codes
0 = Success
1 = Runtime error
2 = File not found  
124 = Timeout

# Trace stack
python3 script.py 2>&1 | grep -A5 "Traceback"

# Verify JSON freshness
cat all_rank.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('earliest_data_date', 'N/A'))"

# Check health status
cat health_status.json | python3 -m json.tool | grep -A2 "fail_count"
```

---

### 📚 External Resources

- [Mark Minervini's Trade Like Buffett](https://www.markminervini.com/books/)
- [FinMind v4 API Integration Guide](https://hermes-agent.nousresearch.com/skills/finmind-api-integration)
- [General Screening Patterns](https://hermes-agent.nousresearch.com/skills/financial-market-screening)

---

*Author: Pascal Wang | Last Updated: 2026-05-08 | Version: v2026.05.08*
