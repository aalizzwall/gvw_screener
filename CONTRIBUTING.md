# 貢獻指南 (Contributing Guide)

感謝您對 **GVW Screener (Garrett Van Wagoner 台股量化選股與即時監控系統)** 的關注與貢獻！
無論是回報問題、提出功能建議、改善文檔或提交代碼修復，我們都非常歡迎。

請在參與貢獻前參閱本指南，確保協作流程順暢且品質一致。

---

## 🌟 行為準則 (Code of Conduct)

參與本專案的所有成員均需遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。請保持友善、包容與互相尊重的交流環境。

---

## 🐛 回報問題與提出建議 (Issues)

在提交 Issue 前，請先搜尋 [現有 Issues](https://github.com/aalizzwall/gvw_screener/issues) 確認是否已有相同或相關討論：

* **錯誤回報 (Bug Report)**：請使用 [Bug Report 範本](https://github.com/aalizzwall/gvw_screener/issues/new?template=bug_report.yml)，詳細提供受影響模組、重現步驟與日誌資訊。
* **功能建議 (Feature Request)**：請使用 [Feature Request 範本](https://github.com/aalizzwall/gvw_screener/issues/new?template=feature_request.yml)，描述需求動機、使用情境與架構構想。
* **安全性問題 (Security Vulnerability)**：**切勿在公開 Issue 中揭露**，請循 [私密安全通報管道](https://github.com/aalizzwall/gvw_screener/security/advisories/new) 提交。

---

## 💻 本地開發與環境設置 (Development Setup)

### 1. Fork 與 Clone 專案
```bash
git clone https://github.com/<your-username>/gvw_screener.git
cd gvw_screener
```

### 2. 建立虛擬環境並安裝依賴
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 設定環境變數
複製範本並填入開發所需的金鑰（測試時可保留空值或填入 mock 值）：
```bash
cp .env.example .env
```
> [!WARNING]
> `.env` 包含敏感金鑰與 Token，**絕對不要 commit 進 git**。

---

## 🌿 分支命名與提交規範 (Branching & Commits)

### 分支命名慣例
* 新增功能：`feat/short-description`
* 錯誤修復：`fix/short-description`
* 文檔更新：`docs/short-description`
* 雜項與優化：`chore/short-description`

### Commit Message 慣例 (Conventional Commits)
請使用標準 Conventional Commits 格式：
* `feat(...)`: 新功能
* `fix(...)`: 錯誤修復
* `docs(...)`: 文檔變更
* `test(...)`: 新增或調整測試
* `refactor(...)`: 重構（不影響功能）
* `chore(...)`: 構建、配置或雜項更新

---

## 🧪 測試與代碼品質規範 (Testing & Quality)

在發起 Pull Request 之前，請務必在本地完成以下檢查：

### 1. 執行單元測試
```bash
python3 -m unittest discover -s tests -v
```

### 2. 語法編譯檢查
```bash
python3 -m py_compile $(find src gvw_bot tests -name "*.py")
```

### 3. 機敏資訊檢驗
確保程式碼中沒有任何寫死的真實 API Token、密鑰或私有伺服器路徑。

---

## 🚀 提交 Pull Request (PR)

1. 將修改推送到您的 Fork 分支：
   ```bash
   git push origin <your-branch-name>
   ```
2. 在 GitHub 建立 Pull Request。
3. 詳細填寫 [Pull Request Template](.github/pull_request_template.md) 中規範的欄位與自檢清單。
4. 確保 GitHub Actions CI 矩陣測試（Python 3.9 與 3.11）全部亮綠燈。
5. 專案維護者將會為您進行 Code Review 並討論合併。

---

## ⚖️ 免責聲明提醒 (Disclaimer)

所有新加入的指標、分析或建議模組，均必須遵循量化研究與學術探討宗旨，**不得宣稱或包裝為保證獲利之投資建議**。所有對外呈現必須保留「僅供參考，非投資建議」的風控免責標註。
