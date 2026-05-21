# FinMind API 負載評估報告

## 📌 執行摘要

基於當前篩選條件，**盤中監控模式 (API 驅動) 在安全範圍內運作**：
- **每小時 API 消耗**: 216 次/小時 (含 gvw_collector)
- **FinMind 限額**: 600 次/小時
- **安全餘量**: 384 次/小時 (64% of quota available)

---

## 📊 基礎數據

| 項目 | 數值 | 備註 |
|------|------|-------|
| Active stocks | 2,005 | gvw_collector 健康庫 |
| High score (≥0.95) | 0 | collector 不使用即時分數 |
| 符合訊號條件標的 | **26** | final_score ≥ 0.95 + 🟢回調買入區 |

### 前 10 大候選標的

```
排名   Ticker  Final Score  Entry Signal
------ ------- ------------ -------------------------
1     8021    0.99         🟢 回調買入區
2     6515    0.99         🟢 回調買入區
3     3167    0.99         🟢 回調買入區
4     5498    0.98         🟢 回調買入區
5     6223    0.98         🟢 回調買入區
...   ...     ...          ...
```

---

## ⏱️ 負載計算邏輯

### 監控週期
- **交易時間**: 9:30 - 13:30 (台股指數交易時段)
- **持續時間**: 4 小時
- **檢查頻率**: 每 10 分鐘一次

### API 呼叫次數

```
計算式：候選標的數量 × 每日檢查次數
       = 26 × (4 小時 / 0.167 小時)
       = 26 × 24
       = 624 次/天
```

### 每小時負載分解

| 組件 | 呼叫頻率 | 時均負載 |
|------|----------|----------|
| gvw_collector | 9:00, 17:00 (每日 2 次) | ~3 次/小時 (平攤) |
| Phase 3 Limit | 最大 60 次/小時 | ⚠️ 上限保護 |
| **gvw_intraday_monitor** | **156 次/小時** | **監控核心負載** |
| **總計** | **~216 次/小時** | **安全範圍內** |

---

## 🛡️ 限流策略層級

### gvw_collector (每日同步)
```python
# 9:00, 17:00 各執行一次，平攤至整日負載
calls_per_day = 2
hourly_rate = calls_per_day / 16      # 約 3 次/小時
```

### Phase 3 Limit (過熱保護)
```python
# 從 global_calls 追蹤總次數
if global_calls >= 60:
    raise RateLimitError("今日 API 上限已達")
```

### gvw_intraday_monitor (監控核心)
```python
# 每 10 分鐘掃描候選標的
INTERVAL_SECONDS = 600           # 10 分鐘
num_candidates = 26
calls_per_day = num_candidates × (4 小時 / (10/60))
                      = 26 × 24
                      = 624 次
```

---

## ⚠️ 安全邊際分析

### 當前狀態
- **已使用**: 216 次/小時
- **剩餘容量**: 384 次/小時
- **使用率**: 36%

### 緩衝空間
```python
安全係數 = FinMind Limit / (monitor + collector)
         = 600 / (156 + 60)
         = 2.79x
        
結論：即使監控頻率加倍，仍在安全範圍內！
```

---

## 💡 優化建議

### 若未來候選標的數增加

| 候選數量 | 每小時負載 | 總負載 (含 collector) | 狀態 |
|----------|------------|----------------------|-------|
| 26 | 156 | 216 | ✅ 安全 |
| 50 | 300 | 360 | ✅ 安全 |
| 74 | 444 | 504 | ✅ 接近上限 |
| 80 | 480 | 540 | ⚠️ 需減緩頻率 |

### 頻率調整機制 (自動適應)

```python
def adjust_frequency(num_candidates):
    """根據候選標的數動態調整檢查頻率"""
    target_hourly = 350  # 目標時均負載 (留 250 給 collector)
    checks_per_day = target_hourly * 4
    interval_minutes = int(24 * num_candidates / checks_per_day * 10)
    return max(10, min(60, interval_minutes))  # 限制在 10-60 分鐘
```

---

## ✅ 結論與行動項目

### 結論
1. **當前配置安全無虞** (使用率 36%)
2. **可承受頻率加倍** (仍低於限額)
3. **無需额外限流機制**

### 行動項目
- [x] 完成 API 負載評估
- [x] 確認 FinMind 限額安全邊際
- [ ] **重構 gvw_intraday_monitor.py 為 API 驅動模式**
- [ ] 更新 Cronjob 排程設定 (每 10 分鐘)

---

## 📝 關鍵指標定義

| 縮寫 | 含義 | 當前值 |
|------|------|--------|
| **num_candidates** | 符合訊號條件的候選標的數 | 26 |
| **INTERVAL_SECONDS** | API 驅動模式檢查間隔 | 600 (10 分鐘) |
| **global_calls_per_hour** | Phase 3 Limit 上限 | 60 |
| **FinMind_Limit** | FinMind API 總限額 | 600 次/小時 |
| **safety_margin** | 剩餘容量 | 384 次/小時 |
