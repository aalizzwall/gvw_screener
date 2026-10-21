# GVW Screener (Garrett Van Wagoner 台股量化選股與即時監控系統)

本專案是一套專為台灣股市設計的量化選股與盤中即時監控系統。核心理念結合了 **Garrett Van Wagoner (GVW) 高成長動能策略** 與 **Mark Minervini Stage 2 趨勢分析**，並整合多資料源自動容錯機制、自主排程引擎與 Telegram Bot 即時互動服務。

---

## 🌟 核心特色

1. **三軌資料源自動容錯 (Failover Architecture)**：
   * **TWSE OpenAPI (第一優先)**：官方開放數據，提供最新損益表、資產負債表與大股東持股。
   * **FinMind API (第二優先 / 註冊等級)**：提供穩定日 K 線、精準財務指標、本益比與大盤數據。
   * **Yahoo Finance (保底備援)**：當前兩者遭遇速率限制或異常時自動降級備援。
2. **完全獨立的自主服務 (`gvw_bot`)**：
   * 不再依賴外部零碎的 cronjob，啟動 `gvw_bot` 即內建常駐排程引擎，自動負責盤前速報、盤中即時掃描、盤後採集與每日總結。
   * 提供 `heartbeat.json` 心跳與健康檢查機制，專供 `hermes-agent` 或看門狗程序進行存活監控。
3. **嚴謹量化選股模型**：
   * **Stage 2 趨勢檢驗**：股價站上 MA200、MA150 > MA200 且 52 週低點回升 > 25%。
   * **大盤相對強弱 (Weighted RS Momentum)**：相對於加權指數的加權超額動能表現。
   * **高品質成長 (Quality Score)**：ROE 高標（$\ge 15\%$）與營收/淨利高成長（YoY $\ge 20\%$）。
   * **分時 K 線三步法驗證**：量能萎縮 $\rightarrow$ 支撐企穩 $\rightarrow$ 帶量突破。

---

## 🏛️ 系統架構

```
gvw_screener/
├── main.py                     # 🌟 統一命令列進入點 (CLI)
├── gvw_bot/                    # 🤖 常駐 Telegram Bot 與排程服務
│   ├── main.py                 # Bot & Daemon 主程式
│   ├── scheduler.py            # 內建自主背景排程器 (GVWScheduler)
│   ├── bot_handler.py          # Telegram 雙向指令監聽
│   └── heartbeat.py            # 心跳健康度記錄器 (供 Hermes 監控)
├── src/
│   ├── config.py               # ⚙️ 全域路徑、環境變數與 TTL 設定
│   ├── core/
│   │   └── orchestrator.py     # 🔄 資料採集調度核心 (Oldest-First 輪詢)
│   ├── providers/              # 🌐 多來源資料適配器 (TWSE / FinMind / Yahoo)
│   │   ├── twse_adapter.py
│   │   ├── finmind_adapter.py
│   │   ├── yahoo_adapter.py
│   │   └── factory.py          # DataProviderFactory (自動容錯調度)
│   ├── state/
│   │   └── state_manager.py    # 💾 持久化狀態管理 (JSON 資料庫)
│   ├── analysis/               # 📊 選股與分析引擎
│   │   ├── ranker.py           # GVW 綜合評分算法
│   │   ├── signals.py          # K 線三步法信號過濾
│   │   └── intraday.py         # 盤中即時監控
│   ├── reporting/              # 📢 報表產製與通報
│   │   ├── report_engine.py    # 篩選日報產製
│   │   └── telegram_notifier.py# Telegram 發報模組
│   └── cli.py                  # CLI 指令綁定
├── systemd/
│   └── gvw_bot.service         # Systemd User 服務設定檔
└── tests/                      # 🧪 自動化測試套件
```

---

## ⏰ `gvw_bot` 內建自主排程機制

啟動 `gvw_bot` 服務後，內建的 [`GVWScheduler`](file:///Users/da042300068/workspace/gvw_screener/gvw_bot/scheduler.py) 會在背景自動執行以下交易日排程（無需配置外部 cron）：

| 任務名稱 | 觸發時間 (週一至五) | 動作說明 |
| :--- | :--- | :--- |
| **晨間選股速報** | `08:30` | 盤前生成昨日排名與注意標的推播至 Telegram |
| **盤中三步法監控** | `09:00 - 13:30`<br/>(每 10 分鐘) | 即時監控前 20 名強勢標的，出現帶量突破即刻警報 |
| **盤後數據採集** | `14:00` | 自動調用 `Orchestrator` 採集全市場收盤與最新財務資料 |
| **全局評分排名** | `15:00` | 運算全市場最新動能 RS、趨勢與品質分 |
| **盤後篩選日報** | `15:30` | 彙整當日篩選結果推播至 Telegram 頻道 |
| **背景快取同步** | 每 60 分鐘 (全天候) | 輪詢更新 10% 最舊資料，確保快取鮮度 |

### 資料來源與新鮮度

- 基本面依序使用 TWSE、FinMind、Yahoo Finance；日線價格依序使用 FinMind、Yahoo Finance。
- Yahoo 日線行情會依序嘗試 `.TW` 與 `.TWO`；連續 3 次價格採集都確認兩者回傳 HTTP 404，該股票才會標記為 `dead` 並從一般輪詢略過。基本面更新成功不會重置價格 404 計數；網路錯誤、429、5xx 不會計入。
- 適配器只會回報可用資料；無資料、格式錯誤或無效價格會觸發下一來源，不會以模擬價格代替。
- 價格資料與基本面分開記錄來源及更新狀態。價格每小時到期後可獨立重抓，不會因基本面 TTL 尚未到期而跳過。
- Telegram 報表只標示「資料截止至」實際納入排名的最舊行情日期；日期缺失時顯示「未知」，不以報表產生時間代替。
- Raspberry Pi 安裝請執行 `pip install -r requirements.txt`；其中包含 Yahoo adapter 非同步串流所需的 `aiohttp`。

### Hermes-Agent 監控接縫 (Health Monitoring)
`hermes-agent` 僅需監控 `gvw_bot` 服務的存活與心跳：
* **心跳檔案**：[`gvw_bot/heartbeat.json`](file:///Users/da042300068/workspace/gvw_screener/gvw_bot/heartbeat.json)（每 30 秒自動更新）
* **探針指令**：
  ```bash
  python main.py bot --status
  ```
  * 正常：Exit Code `0`，顯示 `Healthy: ✅ Yes`
  * 異常：Exit Code `1`，顯示 `Healthy: ❌ No`，此時 Hermes 可執行 `systemctl --user restart gvw_bot` 重啟。

---

## 🎯 GVW 量化選股核心算法

### 1. Stage 2 趨勢分析
* 股價絕對門檻：必須站上 200MA。
* 均線多頭排列：$\text{Price} > \text{MA150} > \text{MA200}$。
* 長期均線向上：MA200 當前斜率大於 20 日前斜率。
* 遠離底部：當前價格距 52 週最低點 $> 25\%$。

### 2. 加權相對強弱動能 (Weighted RS)
計算相對於台灣加權指數（TAIEX）的加權超額報酬：
$$\text{Weighted RS} = 0.2 \times \text{RS}_{3M} + 0.3 \times \text{RS}_{6M} + 0.5 \times \text{RS}_{12M}$$

### 3. 品質與成長性評分 (Quality Score)
* **ROE 評分**：$\min(1.0, \text{ROE} / 15\%)$。
* **成長乘數**：營收與淨利 YoY 成長率 $\ge 20\%$ 為核心指標。

### 4. 綜合排名公式
$$\text{Final Score} = (\text{Momentum Rank} \times 40\%) + (\text{Trend Score} \times 30\%) + (\text{Quality Rank} \times 30\%)$$

### 5. 分時 K 線三步法 (K-Line Verification)
1. **量能萎縮**：近 5 日均量 $\le$ 20 日均量之 $70\%$。
2. **支撐企穩**：近 3 日價格振幅 $\le 3\%$。
3. **帶量突破**：當日收盤價突破前 5 日高點，且當日成交量 $> 1.5 \times$ 20 日均量。

---

## 🚀 快速上手

### 1. 環境設定
在專案根目錄建立 [`.env`](file:///Users/da042300068/workspace/gvw_screener/.env) 檔案：
```ini
# FinMind API Token (註冊等級可享每小時 600 次請求限額)
FINMIND_TOKEN="您的172字元Token"

# Telegram Bot 推播憑證
TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
TELEGRAM_HOME_CHANNEL="-100123456789"

# (可選) Telegram 個股 AI 分析；使用 OpenAI API，費用依帳戶用量計算
OPENAI_API_KEY="您的OpenAI API Key"
OPENAI_MODEL="gpt-4o-mini"
AI_ANALYSIS_ALLOWED_CHAT_IDS="您的Telegram Chat ID，可用逗號分隔多個"

# (可選) 自訂資料儲存目錄，預設為專案目錄下的 data/
GVW_DATA_DIR="./data"
```

### 2. 常用命令列指令 (CLI)

```bash
# 啟動 gvw_bot 常駐排程與 Telegram 服務
python main.py bot

# 檢查 gvw_bot 運行狀態與心跳 (Hermes 探針指令)
python main.py bot --status

# 一鍵執行完整管線 (採集 ➔ 評分 ➔ 產報)
python main.py pipeline --send

# 單獨執行資料採集 (Oldest-First 輪詢)
python main.py collect

# 單獨執行選股評分排名
python main.py rank

# 產製並列印每日篩選報告 (加上 --send 同步發至 Telegram)
python main.py report --send

# 執行盤中即時信號掃描
python main.py monitor --send

# 工具箱指令
python main.py tools verify-token   # 驗證 FinMind Token 與所屬等級
python main.py tools check-health   # 檢查 TWSE / FinMind / Yahoo 連線
python main.py tools force-update   # 強制刷新全市場歷史價格
```

### 3. Telegram 互動指令

Bot 提供常駐鍵盤按鈕與 Telegram 指令選單；也可直接輸入下列指令：
* `/report`：立即產製並傳送最新篩選報告
* `/status`：查詢 Bot 服務運行狀態、PID 與心跳
* `/jobs`：查看內建排程各任務之最後執行時間與下次時間
* `/monitor`：立即執行一次盤中 K 線三步法掃描
* `/collect`：手動觸發資料採集輪詢
* `/rank`：手動觸發重新評分排名
* `/analyze 2330`：使用 OpenAI 根據本機已採集的個股資料產生 AI 評估；也可點鍵盤「🤖 AI 個股分析」後輸入代碼。

AI 分析只使用本機已保存的排名、最多 20 筆近期日線、基本資料與財務資料；這些股票資料會傳送至 OpenAI API，請先確認符合你的資料使用需求。必須設定 `OPENAI_API_KEY` 和 `AI_ANALYSIS_ALLOWED_CHAT_IDS` 才能使用；白名單預設為空，未授權的聊天不會觸發模型請求。AI 回覆僅供參考，非投資建議；不會改變量化排名或送出交易委託。

---

## 🧪 自動化測試

本專案附帶完整單元測試（包含 Provider 連線心跳、資料正規化、排名演算法與排程觸發邏輯）：

```bash
python3 -m unittest discover tests/
# Ran 10 tests in 0.111s (OK)
```

---

## ⚙️ Systemd 服務部署 (Raspberry Pi / Linux)

若在伺服器上部署，可使用內建的 Systemd 服務設定：

```bash
# 1. 複製服務檔至使用者 systemd 目錄
mkdir -p ~/.config/systemd/user
cp systemd/gvw_bot.service ~/.config/systemd/user/

# 2. 啟用並啟動服務
systemctl --user daemon-reload
systemctl --user enable --now gvw_bot

# 3. 檢查運行日誌
journalctl --user -u gvw_bot -f
```

## 🚀 GitHub Actions 自動部署 (Self-hosted Runner)

`.github/workflows/deploy.yml` 會在 Pull Request 上執行測試；推送至 `main` 且測試通過後，會將程式部署到 production runner、安裝依賴並重啟 `gvw_bot`。手動執行 workflow 也只會在 `main` 分支部署。

一次性設定：

1. 在 GitHub Repository 的 **Settings → Actions → Runners**，確認 runner 帶有 `self-hosted`、`Linux`、`ARM64` labels。runner 必須在部署主機上以 `pi` 使用者執行；workflow 會拒絕其他使用者。
2. 在 Raspberry Pi 安裝部署需求：
   ```bash
   sudo apt update
   sudo apt install -y rsync python3-venv
   ```
3. 在主機建立 `/home/pi/.hermes/gvw_screener/.env`，設定所需的 Telegram 與資料來源憑證，並限制檔案權限：
   ```bash
   mkdir -p /home/pi/.hermes/gvw_screener
   nano /home/pi/.hermes/gvw_screener/.env
   chmod 600 /home/pi/.hermes/gvw_screener/.env
   ```
   部署會保留 `.env`、`data/`、`logs/` 與資料庫檔案。
4. 讓 `pi` 的 systemd user manager 在登出後仍運作：
   ```bash
   sudo loginctl enable-linger pi
   ```
5. 推送 workflow 後，首次部署會安裝 user service、建立 `.venv`、安裝依賴並啟動服務。

Workflow 僅對 `main` 的 push 和手動執行觸發部署，不會在 Pull Request 中執行 self-hosted runner。請保護 `main` 分支，並限制可修改 workflow 或合併程式碼的人員。

若部署目錄沒有 `universe.json`，首次採集會從 TWSE 與 TPEx 官方 OpenAPI 載入上市及上櫃公司清單並保存。任一來源不可用或回傳無效資料時，採集工作會報錯，不會寫入不完整清單。
