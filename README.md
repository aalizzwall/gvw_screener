# GVW Screener System (Garrett Van Wagoner Strategy)

## 📌 Core Specification (Main Specification Document)

**📘 [`GVW-CORE-SPEC.md`](GVW-CORE-SPEC.md)** is the **central hub** for the GVW ecosystem. It defines:
- Layer 1 (Core Spec): Strategy pillars, health metrics, and implementation guardrails
- Layer 2-5 Architecture Map: References to Python scripts in `/home/pi/.hermes/gvw_screener/`
- External Tools: Cross-references to [`financial-market-screening`](https://hermes-agent.nousresearch.com/skills/financial-market-screening) and [`finmind-api-integration`](https://hermes-agent.nousresearch.com/skills/finmind-api-integration)

> **Usage Tip**: For AI agents or team members, load `gvw-core-spec` skill first to understand the system architecture. It references all Python implementations without duplicating them.

---

## 📋 System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    gvw_screener/ Project Structure                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  📘 GVW-CORE-SPEC.md        # Layer 1: Core Spec (Strategy + Guardrails)         │
│     └─ References all Python scripts and external tools             │
│                                                                      │
│  ├── gvw_ranker.py         # Layer 2: Strategy Implementation        │
│  │      (RS / Stage 2 / Quality Scoring, Entry Signals)             │
│                                                                      │
│  ├── gvw_collector.py      # Layer 3: Data Infrastructure            │
│  │      (Tiered TTL, Dead-stock Isolation, API Wrapper)             │
│  │      ↗ References finmind-api-integration skill                   │
│                                                                      │
│  ├── gvw_reporter.py       # Layer 5: Reporting & Analysis           │
│  └── gvw_intraday_monitor.py # Layer 4: Operations & Automation      │
│             (Real-time K-line alerts, Signal deduplication)         │
│                                                                      │
│  └── gvw_signal_detector.py # Shared utility for K-line verification│
│                              (Used by Reporter & Intraday Monitor)   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## ⏰ Cronjob Schedule

| Task | Python Script | Cron Expression | Purpose | Delivery Mode |
|------|---------------|-----------------|---------|---------------|
| Data Collection | `gvw_collector.py` | `0 * * * *` (Every hour) | Tier 1→2→3 TTL refresh, dead-stock isolation | `local` |
| Ranking | `gvw_ranker.py` | `20 8 * * *` (Daily 08:20) | Calculate scores + track `earliest_data_date` | `origin` |
| Daily Report | `gvw_reporter.py` | `30 8 * * *` (Daily 08:30) | Generate report + Direct API push | `local` |
| Intraday Monitor | `gvw_intraday_monitor.py` | `20,50 9-14 * * *` | Real-time K-line breakout alerts | `local` |

## 🎯 GVW Strategy Logic (GVW Standard)

### 1️⃣ Trend Analysis (Stage 2 Detection)
```python
# Absolute threshold: Must be above SMA200
is_stage_2 = (price > sma200) AND \
             (price > sma150 > sma200) AND \
             (sma200_slope_positive) AND \
             (price > 52w_low * 1.25) AND \
             (price < 52w_high * 1.30)
```

### 2️⃣ Growth & Quality (Hyper-Growth)
```python
# ROE performance scoring (not hard filtering)
roe_score = min(1.0, roe / 0.15)  # ROE >= 15% → full points

# Growth ≥ 20% is core standard
growth_multiplier = max(0.3, 0.5 + 0.2 * growth/20)

# Composite score
quality_score = roe_score × growth_multiplier
```

### 3️⃣ Entry Timing (Pullback Buy Zone)
```python
# 🟢 Pullback Buy Zone: Near MA20/MA50 support (±3-8%)
buy_zone_price = price_within(3%, 8%, MA20_or_MA50)

# Avoidance Zones:
# - 🔴 Overextended: price > MA + 8% (wait for pullback)
# - 🟡 Weak trend: price < critical MAs
```

## 🔧 Quantitative Ranking Formula

```python
final_score = (momentum_rank × 40%) + \
              (trend_score × 30%) + \
              (quality_rank × 30%)
```

**⚠️ Important**: `momentum_rank` must be recalculated (price-dependent), while `trend` and `quality` can be reused across rankings.

## 📊 K-line Three-Step Verification

Implemented in `gvw_signal_detector.py`, called by `gvw_reporter.py` and `gvw_intraday_monitor.py`:

```
1️⃣ Volume Dry-up (量能萎縮):
   - Last 5 days avg volume ≤ 70% × 20 days avg volume
   
2️⃣ Bottoming Out (支撐企穩):
   - Price range of last 3 days ≤ 3% of current price
   
3️⃣ Breakout (帶量突破):
   - Current close > max of previous 5 days (excluding today) ✅
   - Current volume > 1.5 × 20 days avg volume
```

**Intraday Monitoring Flow:**
1. Filter `all_rank.json` for `final_score >= 0.95` AND `'🟢 Pullback Buy Zone'` entries
2. Execute K-line three-step verification
3. If passed and triggered within 24h window → **Direct Telegram API alert**

## 📁 Output Structure

### all_rank.json (Ranking Results)
```json
{
  "earliest_data_date": "2024-03-15T09:30:00",  // ⚠️ Sync health indicator (excludes dead-stock)
  "data": [
    {
      "ticker": "6543",
      "name": "XX Corp",
      "final_score": 0.227,
      "momentum_rank": 0.85,
      "trend_score": 0.92,
      "quality_rank": 0.31,
      "entry_signal": "🟢 Pullback Buy Zone",
      "is_gvw_strict": true
    }
  ]
}
```

## ⚠️ Pitfalls & Lessons Learned

| Issue Type | Anti-Pattern | Best Practice |
|------------|--------------|---------------|
| **ROE Trap** | ❌ ROE < 10% → exclude | ✅ Use scoring: `roe_score = min(1.0, roe/0.15)` |
| **Growth Filtering** | ❌ Growth < 20% → reject | ✅ Growth ≥ 20% is core; scale score otherwise |
| **Indentation** | ❌ Code after `if:` at wrong level | ✅ Initialize vars before loop, place post-loop logic outside |
| **Dead Stock** | ❌ Keep calling API on failed tickers | ✅ Track `fail_count`, set `dead` after 3 consecutive failures |
| **Cronjob Prompting** | ❌ "Please run GVW ranking calculation" | ✅ Use exact command: `python3 /home/pi/.hermes/gvw_screener/gvw_ranker.py` |
| **Delivery** | ❌ Use Gateway for high-freq alerts | ✅ Use Direct API calls for instant alerts to avoid noise |

## 🚀 Deployment Commands

- **Data Collection**: `python3 /home/pi/.hermes/gvw_screener/gvw_collector.py` (Every hour)
- **Ranking**: `python3 /home/pi/.hermes/gvw_screener/gvw_ranker.py` (Daily 08:20)
- **Daily Report**: `python3 /home/pi/.hermes/gvw_screener/gvw_reporter.py` (Daily 08:30)
- **Intraday Monitoring**: `python3 /home/pi/.hermes/gvw_screener/gvw_intraday_monitor.py` (09:00-14:00)

## 📚 Skills Reference

| Skill | Category | Usage |
|-------|----------|-------|
| [`gvw-core-spec`](https://hermes-agent.nousresearch.com/skills/gvw-core-spec) | devops (GVW) | **Main hub for AI agents** - Load first to understand architecture |
| [`financial-market-screening`](https://hermes-agent.nousresearch.com/skills/financial-market-screening) | devops | General market screening patterns (Tiered TTL, Health Isolation) |
| [`finmind-api-integration`](https://hermes-agent.nousresearch.com/skills/finmind-api-integration) | devops | FinMind v4 API wrapper and dataset mapping |

> **AI Usage Pattern**: Load `gvw-core-spec` → AI will reference Python scripts in `/home/pi/.hermes/gvw_screener/` without duplication. No need to load additional fragmented skills.

## 📜 Version History

| Version | Date | Changes |
|---------|------|---------|
| v1.0 | May 6, 2026 | Initial commit with core files + tiered TTL strategy |
| v1.1 | May 7, 2026 | Direct API Push for Reporter & Intraday Monitor; Decouple Logic/Delivery |
| v1.2 | May 7, 2026 | Re-architect `gvw-core-spec` into layered structure (Layer 1 references Layers 2-5); Clean up fragmented skills |

## 🔗 Related Resources

- **Mark Minervini's Trade Like Buffett** - [Original GVW Strategy Source](https://www.markminervini.com/books/)
- **FinMind v4 Dataset Map** - See [`finmind-api-integration`](https://hermes-agent.nousresearch.com/skills/finmind-api-integration#dataset-mapping)

---

**Author**: Pascal Wang  
**Last Updated**: 2026-05-07  
**Repository**: `github.com/aalizzwall/gvw_screener`
