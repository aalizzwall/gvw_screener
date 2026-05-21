# 🎯 GVW Screener System Logic Summary (v2026.05.08)

## 📋 版本歷史
| Version | Commit Hash | Description | Date |
|---------|-------------|-------------|------|
| v2026.05.08 | `a111e5f` | Fix global oldest sync timestamp & enhance health monitoring | Now |
| v2026.05.08.post | `4e91976` | Add token validation notice & cleanup runtime artifacts | Now |

---

## 🏗️ 系統架構 (5 Layers)

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Core Spec (GVW-CORE-SPEC.md)                        │
│   → Defines strategy pillars, health metrics, guardrails     │
└─────────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Strategy Implementation (gvw_ranker.py)             │
│   → MomentumRank(40%) + TrendScore(30%) + QualityRank(30%)   │
│   → Scoring-based (no hard thresholds)                       │
└─────────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Data Infrastructure (gvw_collector.py)              │
│   → Tiered TTL: High=4h, Medium=24h, Low=7d                  │
│   → Dead-stock isolation (3 failures → dead)                 │
│   → Rate limiting + exponential backoff                      │
└─────────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Operations & Automation                             │
│   → Collector: Every hour (local)                            │
│   → Ranker: Daily 08:20 (origin)                             │
│   → Reporter: Daily 08:30 (local + Direct API)               │
│   → Intraday Monitor: Mon-Fri 09:00-14:00 (Direct API)       │
└─────────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: Reporting & Analysis                                │
│   → Strong Buy Signals (K-line 3-step)                       │
│   → Pullback Top 10 (MA20/MA50 buy zones)                    │
│   → Strict Candidates (<52w_high × 1.30)                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 🧮 評分公式與邏輯

### Core Formula
```python
final_score = 0.4 × MomentumRank + 0.3 × TrendScore + 0.3 × QualityRank
```

### Component Breakdown

#### 1️⃣ **Momentum Rank (40%)** - Relative Strength vs TAIEX
```python
# RS over multiple windows:
ret_3m = (price[-1] / price[-63]) - 1      # Short-term momentum
ret_6m = (price[-1] / price[-126]) - 1     # Mid-term momentum  
ret_12m = (price[-1] / price[-252]) - 1    # Long-term momentum

# Composite momentum with market comparison:
abs_momentum = (ret_3m + ret_6m + ret_12m) / 3
relative_strength = ret_12m - market_ret_12m
boosted_momentum = abs_momentum × (1 + max(0, relative_strength))
```

#### 2️⃣ **Trend Score (30%)** - Minervini Stage 2
```python
# Criteria (scoring system):
- Price > MA150:              +30% score
- MA150 > MA200:              +30% score
- MA200 slope positive:       +20% score
- Price / 52w_low > 1.25:     +20% score

# Strict Stage 2 Check:
is_strict_trend = (
    price > ma150 and 
    ma150 > ma200 and 
    ma200_slope_positive and 
    price/low_52w > 1.25 and 
    price/high_52w < 1.30
)
```

#### 3️⃣ **Quality Rank (30%)** - ROE × Growth
```python
# ROE Score:
roe_score = min(1.0, roe / 0.15)  # Max score at ROE >= 15%

# Growth Score (Revenue & Net Income):
if growth < 20%:
    if growth > -10%:
        growth_score *= max(0.3, 0.5 + 0.2 × growth)
    else:
        growth_score *= 0.1  # Significant decline
else:
    growth_score *= 1.0

quality_score = roe_score × growth_score
```

---

## 📊 Tiered TTL Strategy (Data Freshness Management)

| Score Range | TTL Hours | Refresh Frequency | Purpose |
|-------------|-----------|-------------------|---------|
| > 0.9 (High) | 4 hours   | Every 6 hours     | Keep top candidates fresh |
| 0.6-0.9 (Medium) | 24 hours | Daily            | Moderate refresh |
| < 0.6 (Low)   | 7 days    | Weekly            | Save API calls |

**Dead-stock Logic:**
```python
# Dead stocks: fail_count >= 3 consecutive failures
- Excluded from all_sync calculation (via sync_date = None)
- Avoid false "data stale" signals for dead stocks
- Re-check after 180 days (RECHECK_INTERVAL)
```

---

## 🛡️ Implementation Guardrails

### ✅ DOs:
- Use scoring system over hard thresholds
- Apply growth-first logic (growth >= 20% is core)
- Implement dead-stock isolation with fail tracking
- Guard financial ratio denominators (net_income > 0, equity > 0)
- Use Direct Telegram API for urgent alerts

### ❌ DON'Ts:
- Never exclude stocks based on `ROE < X%` or similar hard rules
- Never divide by potentially negative values without guards
- Never mark stock as dead after single failure (needs 3 consecutive)

---

## 🔧 Entry Signal Logic

```python
# Pullback Buy Zone Calculation:
dist20 = (price / MA20) - 1   # Distance from 20-day MA
dist50 = (price / MA50) - 1   # Distance from 50-day MA

# Use tighter MA for buy zone:
min_dist = min(dist20, dist50)

# Signals:
- 🟢 "回調買入區": -5% <= price < MA <= +12% (Sweet spot!)
- 🟡 "過熱 - 待回調": > +15% (Avoid buying climax)
- 🔴 "趨勢偏弱": < -5% (Below support, weak trend)
- ⚪ "中立/觀察": Near MA but no clear signal
```

---

## 📁 Key Files & Purposes

| File | Purpose | Last Modified |
|------|---------|---------------|
| `gvw_collector.py` | Data collection with tiered TTL | v2026.05.08 |
| `gvw_ranker.py` | Core ranking algorithm | v2026.05.08 |
| `gvw_signal_detector.py` | K-line pattern recognition | - |
| `gvw_reporter.py` | Telegram report generation | - |
| `GVW-CORE-SPEC.md` | Architecture specification | v1b |
| `CHANGELOG.md` | Version history | New |
| `RELEASE_NOTES.md` | Operational guides | New |
| `universe.json` | Stock universe (excl. 00/02 prefix) | Auto-generated |
| `all_rank.json` | Latest ranking results | Updated by ranker |
| `health_status.json` | Dead-stock tracking | Updated by collector |
| `market_index.json` | TAIEX market data | Daily update |

---

## 🚨 Critical Alert: FINMIND TOKEN

**Status**: ⚠️ Token Expired (400 "Token illegal")

**Current State:**
- ✅ Token length: 172 chars (correct format)
- ❌ API returns 400 error: "Token illegal or parameter error"

**Resolution Steps:**
```bash
# 1. Get new token from FinMind portal
https://financial.stanford.edu

# 2. Update /home/pi/.hermes/.env:
export FINMIND_TOKEN=<paste_new_172_char_token_here>

# 3. Verify token length:
python3 -c "import os; print(len(os.getenv('FINMIND_TOKEN')))"
# Should output: 172
```

---

## 📋 Diagnostics Workflow

```bash
# 1. Check exit codes
0 = Success
1 = Runtime error
2 = File not found
124 = Timeout

# 2. Trace stack for errors
python3 gvw_ranker.py 2>&1 | grep -A5 "Traceback"

# 3. Verify data freshness (excl. dead stocks)
cat all_rank.json | python3 -c "import json,sys; d=json.load(sys.stdin); print('Earliest Data:', d.get('earliest_data_date', 'N/A'))"

# 4. Check health status (dead stocks count)
python3 -c "import json; h=json.load(open('health_status.json')); dead=[k for k,v in h.items() if v.get('status')=='dead']; print(f'Dead stocks: {len(dead)}')"

# 5. Verify data coverage
cat universe.json | python3 -c "import json,sys; u=json.load(sys.stdin); d=len(open('.hermes/gvw_screener/data').name); print(f'Coverage: {d}/{len(u)} ({100*d/len(u):.1f}%)')"

```

---

## 🔍 Quick Command Reference

```bash
# Run full screening cycle
python3 gvw_collector.py    # Data refresh (silent, hourly)
python3 gvw_ranker.py        # Ranking analysis (daily 08:20)
python3 gvw_reporter.py      # Report generation (daily 08:30)

# Check latest ranking results
cat all_rank.json | python3 -m json.tool | head -50

# Monitor dead stocks
cat health_status.json | python3 -c "import json,sys; d=json.load(sys.stdin); import json; print(json.dumps({k:v for k,v in d.items() if v.get('status')=='dead'}, indent=2))"

# View commit history
git log --oneline -10

# Check for uncommitted changes
git status
```

---

## 📚 External References

- [Mark Minervini's Trade Like Buffett](https://www.markminervini.com/books/)
- [FinMind v4 API Documentation](https://hermes-agent.nousresearch.com/skills/finmind-api-integration#dataset-mapping)
- [General Market Screening Patterns](https://hermes-agent.nousresearch.com/skills/financial-market-screening)

---

## ✅ 當前系統狀態

- **Core Logic**: ✅ Stable (v2026.05.08)
- **Health Monitoring**: ✅ Implemented
- **Dead-stock Isolation**: ✅ Working
- **Tiered TTL**: ✅ Active
- **Data Freshness**: ⚠️ Needs API token refresh
- **Token Status**: ❌ Expired (requires renewal)

---

*Author: Pascal Wang | System Architect | Last Updated: 2026-05-08 | Version: v2026.05.08*
