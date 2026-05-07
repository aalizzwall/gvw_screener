# GVW Screener System (Garrett Van Wagoner Strategy)

## 📋 系統架構總覽

```
gvw_screener/
├── gvw_collector.py        # 資料採集（Tier 1-3 TTL）→ 每小時執行 (Hermes Cronjob)
├── gvw_ranker.py           # 排名計算 + 最舊資料追蹤 → 08:20 (Hermes Cronjob)  
├── gvw_reporter.py         # 報告生成 + 直接 API 推送 → 08:30 (Hermes Cronjob, Local Deliver)
├── gvw_intraday_monitor.py # 即時 K 線突破監控 → 09:00-14:00 (:20/:50) (Hermes Cronjob, Local Deliver)
├── gvw_signal_detector.py  # K 線三步檢查支援邏輯
├── all_rank.json           # 排名結果輸出（包含 earliest_data_date）
├── health_status.json      # 健康狀態追蹤（dead-stock 隔離）
└── pointer.json            # Universe cache（4-digit only）
```

## ⏰ Cronjob 時間表

| 任務 | 程式檔 | cron expression | 說明 | 推送方式 |
|------|--------|-----------------|------|-----------|
| 資料採集 | `gvw_collector.py` | `0 * * * *` (每小時) | Tier 1→2→3 TTL，最舊資料優先更新 | Local |
| 排名計算 | `gvw_ranker.py` | `20 8 * * *` (每日 08:20) | 計算排名 + 紀錄全局最舊時間 | Origin |
| 報告生成 | `gvw_reporter.py` | `30 8 * * *` (每日 08:30) | 三欄位報告 $\rightarrow$ **直接 API 推送** | Local |
| 即時監控 | `gvw_intraday_monitor.py` | `20,50 9-14 * * *` | K 線三步突破 $\rightarrow$ **即時 API 警報** | Local |

## 🎯 GVW 策略邏輯（GVW Standard）

### 1️⃣ Trend Analysis (Stage 2 Detection)
```python
# 絕對門檻：必須在 SMA200 之上
is_stage_2 = (price > sma200) AND \
             (price > sma150 > sma200) AND \
             (sma200_slope_positive) AND \
             (price > 52w_low * 1.25) AND \
             (price < 52w_high * 1.30)
```

### 2️⃣ Growth & Quality (Hyper-Growth)
```python
# ROE 效能評分（非硬過濾）
roe_score = min(1.0, roe / 0.15)  # ROE >= 15% → full points

# 成長率 ≥ 20% 是核心標準
growth_multiplier = max(0.3, 0.5 + 0.2 * growth/20)

# 綜合分數
quality_score = roe_score × growth_multiplier
```

### 3️⃣ Entry Timing (Pullback Buy Zone)
```python
# 🟢 回調買入區：靠近 MA20/MA50 支撐（±3-8%）
buy_zone_price = price_within(3%, 8%, MA20_or_MA50)

# Avoidance Zones:
# - 🔴 overextended: price > MA + 8% (wait for pullback)
# - 🟡 weak_trend: price < critical MAs
```

## 🔧 Quantitative Ranking Formula

```python
final_score = (momentum_rank × 40%) + \
              (trend_score × 30%) + \
              (quality_rank × 30%)
```

**⚠️ 重要：momentum rank 需要重新計算（因為價格變動），但 trend/quality 可以 reuse。**

## 📊 K 線三步檢查（GVW Signal Detector）

由 `gvw_signal_detector.py` 提供邏輯，並由 `gvw_reporter.py` 及 `gvw_intraday_monitor.py` 調用：

```
1️⃣ 量能萎縮 (Volume Dry-up):
   - 最近 5 日平均成交量 ≤ 70% × 20 日平均成交量
   
2️⃣ 支撐企穩 (Bottoming Out):
   - 最後 3 日的價格區間 ≤ 當前價的 3%
   
3️⃣ 帶量突破 (Breakout):
   - 當前收盤價 > 過去 5 日（不含當日）最高價 ✅
   - 當前成交量 > 1.5 × 20 日平均成交量
```

**即時監控流程：**
1. 篩選 `all_rank.json` 中 `final_score >= 0.95` 且處於「回調買入區」之標的。
2. 執行 K 線三步檢查。
3. 若通過且為 24 小時內首次觸發 $\rightarrow$ **直接調用 Telegram API 發送即時警報**。

## 📁 Output Structure

### all_rank.json（排名結果）
```json
{
  "earliest_data_date": "2024-03-15T09:30:00",  // ⚠️ 全局同步健康指標 (排除 dead-stock)
  "data": [
    {
      "ticker": "6543",
      "name": "XX Corp",
      "final_score": 0.227,
      "momentum_rank": 0.85,
      "trend_score": 0.92,
      "quality_rank": 0.31,
      "entry_signal": "🟢 回調買入區",
      "is_gvw_strict": true
    }
  ]
}
```

## ⚠️ Pitfalls & Lessons Learned

| 問題類型 | Anti-Pattern | Best Practice |
|---------|--------------|---------------|
| **ROE 陷阱** | ❌ ROE < 10% → exclude | ✅ Use scoring: `roe_score = min(1.0, roe/0.15)` |
| **成長過濾** | ❌ Growth < 20% → reject | ✅ Growth ≥ 20% is core; scale score otherwise |
| **Indentation** | ❌ Code after `if:` at wrong level | ✅ Initialize vars before loop, place post-loop logic outside |
| **Dead Stock** | ❌ Keep calling API on failed tickers | ✅ Track `fail_count`, set `dead` after 3 consecutive failures |
| **Cronjob Prompting** | ❌ "請執行 GVW 排名計算" | ✅ "請在終端機執行 `python3 /home/pi/.hermes/gvw_screener/gvw_ranker.py`" |
| **Delivery** | ❌ Use Gateway for high-freq alerts | ✅ Use Direct API calls for instant alerts to avoid noise |

## 🚀 Deployment
- **Collector**: `python3 /home/pi/.hermes/gvw_screener/gvw_collector.py` (Every hour)
- **Ranker**: `python3 /home/pi/.hermes/gvw_screener/gvw_ranker.py` (Daily 08:20)
- **Reporter**: `python3 /home/pi/.hermes/gvw_screener/gvw_reporter.py` (Daily 08:30)
- **Intraday**: `python3 /home/pi/.hermes/gvw_screener/gvw_intraday_monitor.py` (09:00-14:00)

## 📜 Version History

| Version | Date | Changes |
|---------|------|---------|
| v1.0 | May 6, 2026 | Initial commit with core files + tiered TTL strategy |
| v1.1 | May 7, 2026 | Implement Direct API Push for Reporter & Intraday Monitor; Decouple Logic/Delivery |

## 🔗 Related Resources

- [GVW Stock Screening Skill](https://hermes-agent.nousresearch.com/docs) - For using `gvw-stock-screener-core` skill
- [Mark Minervini's Book: Trade Like Buffett](https://www.markminervini.com/books/) - Original GVW strategy source

---

**Author**: Pascal Wang  
**Last Updated**: 2026-05-07  
