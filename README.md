# GVW Screener System (Garrett Van Wagoner Strategy)

## 📋 系統架構總覽

```
gvw_screener/
├── gvw_collector.py        # 資料採集（Tier 1-3 TTL）→ 08:00 cronjob
├── gvw_ranker.py           # 排名計算 + 最舊資料追蹤 → 08:20 cronjob  
├── gvw_reporter.py         # 報告生成 + K 線訊號檢測 → 08:30 cronjob
├── gvw_signal_detector.py  # K 線三步檢查支援檔案（選配）
├── all_rank.json           # 排名結果輸出（包含 last_update_time）
├── health_status.json      # 健康狀態追蹤（dead-stock 隔離）
└── pointer.json            # Universe cache（4-digit only）
```

## ⏰ Cronjob 時間表

| 任務 | 程式檔 | cron expression | 說明 |
|------|--------|-----------------|------|
| 資料採集 | `gvw_collector.py` | `0 * * * *` (每日 08:00) | Tier 1→2→3 TTL，最舊資料優先更新 |
| 排名計算 | `gvw_ranker.py` | `20 8 * * *` (每日 08:20) | 計算排名 + 紀錄全局最舊時間 |
| 報告生成 | `gvw_reporter.py` | `30 8 * * *` (每日 08:30) | 三欄位報告 + K 線訊號檢查 |

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

在 `gvw_reporter.py` L79-86 或獨立運行的 `gvw_signal_detector.py`：

```
1️⃣ 量能萎縮 (Volume Dry-up):
   - 最近 5 日平均成交量 ≤ 70% × 20 日平均成交量
   
2️⃣ 支撐企穩 (Bottoming Out):
   - 最後 3 日的價格區間 ≤ 當前價的 3%
   
3️⃣ 帶量突破 (Breakout):
   - 當前收盤價 > 過去 5 日（不含當日）最高價 ✅
   - 當前成交量 > 1.5 × 20 日平均成交量
```

**流程：**
1. `gvw_reporter.py` 讀取 `all_rank.json` 中所有「回調買入區」股票
2. 對每一支回調股，檢查 K 線三步是否完成
3. 返回符合條件的股票列表（可單獨發送訊號）

## 📁 Output Structure

### all_rank.json（排名結果）
```json
{
  "last_update_time": "2024-03-15T09:30:00",  // ⚠️ 全局最舊時間
  "data": [
    {
      "ticker": "6543",
      "name": "XX Corp",
      "final_score": 0.227,
      "momentum_rank": 0.85,
      "trend_score": 0.92,
      "quality_rank": 0.31,
      "status": "gvw_strict",  // 或 "pullback_buy_zone"
      "entry_price_range": {"low": 45.2, "high": 47.8},
      "oldest_data_date": "2024-03-15T09:30:00"
    }
  ]
}
```

### 報告格式（Telegram）

**🔥 Top 3 綜合強勢** - 市場領導者  
**🎯 Top 3 回調買入** - 立即可動作機會  
**💎 GVW Strict 名單** - 完整嚴格對齊股票（無分數截斷）  
**⏰ Last Update Time** - 資料最新採集時間點

## ⚠️ Pitfalls & Lessons Learned

| 問題類型 | Anti-Pattern | Best Practice |
|---------|--------------|---------------|
| **ROE 陷阱** | ❌ ROE < 10% → exclude | ✅ Use scoring: `roe_score = min(1.0, roe/0.15)` |
| **成長過濾** | ❌ Growth < 20% → reject | ✅ Growth ≥ 20% is core; scale score otherwise |
| **Indentation** | ❌ Code after `if:` at wrong level | ✅ Initialize vars before loop, place post-loop logic outside |
| **Dead Stock** | ❌ Keep calling API on failed tickers | ✅ Track `fail_count`, set `dead` after 3 consecutive failures |
| **Cronjob Prompting** | ❌ "請執行 GVW 排名計算" | ✅ "請在終端機執行 `python3 /home/pi/.hermes/gvw_screener/gvw_ranker_cron.py`" |
| **Negative ROE** | ❌ Calculate ROE when NI/Equity < 0 | ✅ Guard: `if net_income > 0 and total_equity > 0:` |

## 🚀 Deployment

### 1. Run Collector（每日 08:00）
```bash
python3 /home/pi/.hermes/gvw_screener/gvw_collector.py
```

### 2. Run Ranker（每日 08:20，可 background）
```bash
# Background mode with notification
python3 /home/pi/.hermes/gvw_screener/gvw_ranker_cron.py --notify-on-complete=true &

# Or foreground mode
python3 /home/pi/.hermes/gvw_screener/gvw_ranker.py
```

### 3. Run Reporter（每日 08:30）
```bash
python3 /home/pi/.hermes/gvw_screener/gvw_reporter.py
```

### Cronjob Setup
```bash
crontab -e
# L1: 0 8 * * * cd /home/pi/.hermes/gvw_screener && python3 gvw_collector.py >> /home/pi/.hermes/gvw_screener/collector.log 2>&1
# L2: 20 8 * * * python3 gvw_ranker_cron.py --notify-on-complete=true
# L3: 30 8 * * * python3 gvw_reporter.py >> /home/pi/.hermes/gvw_screener/reporter.log 2>&1
```

## 📜 Version History

| Version | Date | Changes |
|---------|------|---------|
| v1.0 | May 6, 2026 | Initial commit with core files + tiered TTL strategy |

## 🔗 Related Resources

- [GVW Stock Screening Skill](https://hermes-agent.nousresearch.com/docs) - For using `gvw-stock-screener-core` skill
- [Mark Minervini's Book: Trade Like Buffett](https://www.markminervini.com/books/) - Original GVW strategy source

---

**Author**: Pascal Wang  
**Last Updated**: 2026-05-07  
