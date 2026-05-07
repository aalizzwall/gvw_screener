---
name: gvw-core-spec
description: Master specification and architectural index for the Garrett Van Wagoner (GVW) Stock Screening ecosystem. Cross-references Layer 2-5 implementations in /home/pi/.hermes/gvw_screener/ and general tools (financial-market-screening, finmind-api-integration).
tags: [gvw, architecture, specification, index]
---

# GVW Stock Screening Core Specification

> 📌 **Usage Note**: This skill serves as the **Central Hub** for the GVW ecosystem. It defines the "What" and "Why", but references external implementations (Python scripts in `/home/pi/.hermes/gvw_screener/`) for the "How". 

## Layered Architecture

### Layer 1: Core Specification (This Document)
- **Role**: Defines strategy pillars, health metrics, and implementation guardrails.
- **Scope**: `gvw-core-spec` SKILL.md only. No cross-dependencies.

### Layer 2: Strategy Implementation (`/home/pi/.hermes/gvw_screener/gvw_ranker.py`)
- **Pillars**: 
  - **Relative Strength (RS)**: Momentum vs TAIEX over 3m/6m/12m windows
  - **Stage 2 Trend**: Price > MA150 > MA200, SMA200 slope up, near 52w high
  - **Quality Score**: ROE × Growth (with scoring system, NOT hard thresholds)
- **Entry Signal**: Pullback Buy Zone (-5% to +12% from MA20/MA50)
- **Final Score Formula**: `final_score = 0.4×MomentumRank + 0.3×TrendScore + 0.3×QualityRank`

### Layer 3: Data Infrastructure (`/home/pi/.hermes/gvw_screener/gvw_collector.py`)
- **Tiered TTL Strategy**: 
  - Tier 1 (Score > 0.9): 4h refresh
  - Tier 2 (Score 0.6-0.9): 24h refresh
  - Tier 3 (Score < 0.6): 7d refresh
- **Dead-Stock Isolation**: Track `fail_count`, mark as `dead` after 3 failures, exclude from sync
- **API Wrapper**: Rate limiting (0.2-0.6s random sleep), exponential backoff for 5xx errors

📖 **Cross-reference**: See [`finmind-api-integration`](https://hermes-agent.nousresearch.com/skills/finmind-api-integration) for FinMind v4 API details.

### Layer 4: Operations & Automation
- **gvw_collector.py**: Runs every hour via Hermes Cronjob → `deliver: local`
- **gvw_ranker.py**: Daily at 08:20 → `deliver: origin` (for health check)
- **gvw_reporter.py**: Daily at 08:30 → `deliver: local` with **Direct API Push**
- **gvw_intraday_monitor.py**: 09:00-14:00 (:20/:50) → `deliver: local` with **Direct API Alerts**

📖 **Cross-reference**: See [`financial-market-screening`](https://hermes-agent.nousresearch.com/skills/financial-market-screening) for general screening patterns.

### Layer 5: Reporting & Analysis (`gvw_reporter.py`)
- **Strong Buy Signals**: K-line 3-step verified breakouts (Score ≥ 0.95 + Pullback Zone)
- **Pullback Top 10**: Stocks in buy zone with suggested price range (MA20/MA50 ± 3%)
- **Strict Candidates**: GVW Stage 2 strict alignment (Price < 52w_high × 1.30)

## System Health Metrics

### `earliest_data_date` (Sync Health Indicator)
- **Location**: `all_rank.json["earliest_data_date"]`
- **Definition**: Minimum `updated_at` timestamp across all stocks in `data/`
- **Filtering Rule**: Exclude `dead` stocks (from `health_status.json`)
- **Purpose**: 
  - Prevent false synchronization failure signals from dead stocks
  - Detect data gaps for active stocks → trigger Collector repair

### Signal Deduplication (`last_signals.json`)
- Ensures each breakout event is alerted only once within 24h window
- Located in `gvw_intraday_monitor.py` and `gvw_reporter.py`

## Implementation Guardrails (MUST FOLLOW)

### 🛠️ Core "Forbidden List"
1. **Scoring Over Filtering**: Never use hard thresholds (e.g., `ROE < 10% → exclude`). Always use scoring: `roe_score = min(1.0, roe / 0.15)`
2. **Growth-First Logic**: Growth ≥ 20% is core; scale score for lower values: `growth_score = max(0.3, 0.5 + 0.2 * growth/20)`
3. **Dead-Stock Isolation**: Track `fail_count`, mark as `dead` after 3 consecutive failures
4. **Financial Ratio Safety**: Always guard against negative denominators: `if net_income > 0 and total_equity > 0`
5. **Direct API Delivery**: Use Direct Telegram API for instant alerts; set Cronjob `deliver: local` for silent tasks

### 🔍 Diagnostics Workflow
1. Check exit codes: `0`=Success, `1`=Runtime, `2`=File Not Found, `124`=Timeout
2. Trace stack: `python3 script.py 2>&1 | grep -A5 "Traceback"`
3. Verify JSON: `all_rank.json` size (>500KB) + `earliest_data_date` freshness
4. Inspect logs: Look for ✅ (success) or ❌ (failure) markers

## Technical Dependencies

| Path | Purpose |
|------|---------|
| `/home/pi/.hermes/gvw_screener/` | Python implementation scripts |
| `/home/pi/.hermes/gvw_screener/data/*.json` | Cached stock data (tiered TTL) |
| `/home/pi/.hermes/.env` | FinMind token, Telegram credentials |
| [`financial-market-screening`](https://hermes-agent.nousresearch.com/skills/financial-market-screening) | General screening patterns |
| [`finmind-api-integration`](https://hermes-agent.nousresearch.com/skills/finmind-api-integration) | FinMind v4 API wrapper |

---

## 📚 External References

- [Mark Minervini's Trade Like Buffett](https://www.markminervini.com/books/)
- [FinMind v4 Dataset Map](https://hermes-agent.nousresearch.com/skills/finmind-api-integration#dataset-mapping)

---

*Author: Pascal Wang | Last Updated: 2026-05-07*
