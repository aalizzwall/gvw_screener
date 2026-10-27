"""Autopilot: scheduled self-patrol, self-healing, data freshening and post-market review.

Autonomy policy (owner-approved):
- Low-risk actions run automatically (log rotation, lock cleanup, AI failover, re-collection).
- Anything needing code/config change becomes a deduplicated GitHub Issue.
- Weight tuning is only *proposed*; applying needs explicit owner approval.
- Notifications are silent unless something is abnormal / auto-fixed / needs a decision.
"""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from src.autopilot.benchmark import BenchmarkSeries
from src.autopilot.ledger import RecommendationLedger
from src.config import DATA_DIR, TELEGRAM_OWNER_USER_ID
from src.reporting.telegram_notifier import send_telegram_message
from src.state.feedback_queue import FeedbackQueueStore

logger = logging.getLogger("gvw.autopilot")

STALE_RATIO_THRESHOLD = 0.05
ALERT_COOLDOWN_SECONDS = 2 * 3600
ESCALATE_AFTER = 3  # consecutive patrols with the same unresolved issue


class AutoPilot:
    def __init__(
        self,
        state: Any,
        doctor: Any,
        orchestrator: Any,
        ranking_fn: Callable[[], Dict[str, Any]],
        notify: Optional[Callable[..., bool]] = None,
        data_dir: Optional[str] = None,
    ):
        self.state = state
        self.doctor = doctor
        self.orchestrator = orchestrator
        self.ranking_fn = ranking_fn
        self.data_dir = data_dir or DATA_DIR
        self.ledger = RecommendationLedger(state, path=os.path.join(self.data_dir, "recommendations.json"))
        self.benchmark = BenchmarkSeries(path=os.path.join(self.data_dir, "benchmark.json"))
        self.feedback_store = FeedbackQueueStore(path=os.path.join(self.data_dir, "feedback_reports.json"))
        self._notify = notify or send_telegram_message
        self._last_alert: Dict[str, float] = {}
        self._streak: Dict[str, int] = {}
        self.events: List[str] = []  # accumulates for the daily digest

    # ---------- notification ----------
    def notify_owner(self, text: str, reply_markup: Optional[dict] = None) -> bool:
        if not TELEGRAM_OWNER_USER_ID:
            logger.info(f"[autopilot] (no owner configured) {text}")
            return False
        kwargs = {"chat_id": TELEGRAM_OWNER_USER_ID}
        if reply_markup:
            kwargs["reply_markup"] = reply_markup
        return self._notify(text, **kwargs)

    def _record(self, line: str) -> None:
        logger.info(f"[autopilot] {line}")
        self.events.append(line)

    # ---------- issue detection ----------
    def os_health(self) -> List[str]:
        """Raspberry Pi OS level checks; returns human-readable issues."""
        issues: List[str] = []
        try:
            res = subprocess.run(["systemctl", "--failed", "--no-legend", "--plain"],
                                 capture_output=True, text=True, timeout=10)
            failed = [ln.split()[0] for ln in res.stdout.splitlines() if ln.strip()]
            if failed:
                issues.append(f"systemd 失敗單元：{', '.join(failed[:5])}")
        except Exception:
            # Ignore systemctl check failure on non-systemd platforms
            pass
        vcgencmd = shutil.which("vcgencmd")
        if vcgencmd:
            try:
                res = subprocess.run([vcgencmd, "get_throttled"], capture_output=True, text=True, timeout=5)
                value = int(res.stdout.strip().split("=")[-1], 16)
                if value & 0x1:
                    issues.append("電源電壓不足 (under-voltage)，可能造成不穩定")
                if value & 0x4:
                    issues.append("CPU 因過熱/電源被降頻 (throttled)")
            except Exception:
                # Ignore vcgencmd parse or execution failure
                pass
        try:
            usage = shutil.disk_usage("/var/log")
            if usage.total and usage.used / usage.total > 0.85:
                issues.append(f"/var/log 使用率 {usage.used * 100 // usage.total}%")
        except Exception:
            # Ignore /var/log disk usage check failure if directory is unavailable
            pass
        return issues

    def find_issues(self, diag: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        mem, disk = diag.get("memory", {}), diag.get("disk", {})
        if mem.get("percent", 0) > 90:
            issues.append(f"記憶體使用率 {mem['percent']}%")
        if disk.get("percent", 0) > 90:
            issues.append(f"磁碟使用率 {disk['percent']}%")
        temp = diag.get("cpu_temp")
        if temp is not None and temp >= 75:
            issues.append(f"CPU 溫度 {temp}°C")
        if not diag.get("database", {}).get("all_ok", True):
            issues.append("SQLite 完整性檢查失敗")
        hb = diag.get("heartbeat", {})
        if hb.get("exists") and not hb.get("fresh", True):
            issues.append("心跳過久未更新")
        conn = diag.get("connectivity", {})
        for key, label in (("telegram", "Telegram API"), ("twse", "TWSE 證交所")):
            if conn.get(key) and not conn[key].get("ok", True):
                issues.append(f"{label} 連線異常")
        ai = conn.get("ai", {})
        if ai and ai.get("primary") and not ai.get("primary_ok", True):
            issues.append(f"主力 AI ({ai['primary']}) 不可用")
        return issues

    # ---------- jobs ----------
    def intraday_guard(self, light: bool = True) -> Dict[str, Any]:
        """Frequent, lightweight patrol: heal what is safe, alert only on change."""
        diag = self.doctor.run_diagnostics()
        issues = self.find_issues(diag) + self.os_health()
        actions: List[str] = []
        if issues:
            actions = [a for a in self.doctor.run_remediation(light=light) if "無須" not in a]
        seen = set(issues)
        for sig in list(self._streak):
            if sig not in seen:
                self._streak.pop(sig)
        for sig in issues:
            self._streak[sig] = self._streak.get(sig, 0) + 1

        now = time.time()
        fresh_alerts = [s for s in issues if now - self._last_alert.get(s, 0) > ALERT_COOLDOWN_SECONDS]
        if actions:
            self._record("自動修復：" + "；".join(actions))
        if issues:
            self._record("偵測異常：" + "；".join(issues))
        if fresh_alerts:
            for s in fresh_alerts:
                self._last_alert[s] = now
            lines = ["⚠️ <b>【GVW 自主巡檢】偵測到異常</b>"] + [f"• {s}" for s in fresh_alerts]
            if actions:
                lines += ["", "🛠️ <b>已自動處理：</b>"] + [f"• {a}" for a in actions]
            self.notify_owner("\n".join(lines))

        escalated = [s for s, n in self._streak.items() if n == ESCALATE_AFTER]
        for sig in escalated:
            self._escalate(sig, diag)
        feedback_results = self.triage_feedback_reports()
        return {"issues": issues, "actions": actions, "escalated": escalated, "feedback": feedback_results}

    def _evaluate_feedback_via_agent(self, rep: Dict[str, Any], replies_list: List[str]) -> Optional[Dict[str, str]]:
        """
        Uses the autonomous AI agent/LLM to evaluate the last 3 bot replies in the feedback report
        and decide whether the issue can be directly auto-fixed ("refresh_stock" / "ai_failover")
        or requires opening a GitHub Issue ("open_issue").
        """
        try:
            from src.analysis.ai_analyst import _PROVIDERS, _provider_key, _resolve_model, _request_completion
            from src.analysis.model_prober import get_intra_provider_pool

            store = getattr(self.doctor, "ai_provider_store", None)
            if store is None or not hasattr(store, "get_chain"):
                return None

            chain = store.get_chain()
            if not isinstance(chain, list) or not chain:
                return None
            local_url = store.get_local_base_url() if hasattr(store, "get_local_base_url") else ""

            replies_formatted = "\n\n".join(
                f"【倒數第 {len(replies_list) - i} 筆回覆】:\n{r}"
                for i, r in enumerate(replies_list)
            )
            prompt = (
                "你是 GVW 智能選股系統的自主巡檢機器人 (AutoPilot Agent)。\n"
                "請根據以下使用者回報的「最近 3 筆機器人回覆內容」與上下文，評估該問題屬於哪一類：\n"
                "1. `refresh_stock`：特定個股行情、財報、本益比/淨值比/殖利率資料缺漏或過期，可直接強制重抓該個股資料修復。\n"
                "2. `ai_failover`：AI 模型連線失敗 (429/503/timeout)、思考耗盡 Token 或未設定，可直接執行 AI 備援切換與健康重整修復。\n"
                "3. `open_issue`：屬於程式碼邏輯錯誤、排版格式問題 (如 Markdown 未轉粗體) 或需修改程式，應開立 GitHub Issue。\n\n"
                f"- 使用者輸入：{rep.get('user_input') or '無'}\n"
                f"- 補充說明：{rep.get('note') or '無'}\n"
                f"- 偵測個股代碼：{rep.get('ticker') or '無'}\n"
                f"- 最近回覆記錄（最多 3 筆）：\n{replies_formatted}\n\n"
                "請只回傳合法 JSON 格式：\n"
                '{"decision": "refresh_stock|ai_failover|open_issue", "ticker": "4-6位數代號或空字串", "reason": "簡短繁體中文原因與標題"}'
            )

            for entry in chain:
                name, model = entry.split("/", 1) if "/" in entry else (entry, "")
                if name not in _PROVIDERS:
                    continue
                key = _provider_key(name, local_base_url=local_url)
                if not key:
                    continue
                pool = get_intra_provider_pool(name, key, local_base_url=local_url, preferred_model=model)
                for cand in pool:
                    try:
                        actual_model = _resolve_model(name, cand, local_base_url=local_url)
                        raw = _request_completion(
                            name, key, actual_model, [{"role": "user", "content": prompt}], max_tokens=256
                        )
                        start = raw.find("{")
                        end = raw.rfind("}")
                        if start != -1 and end != -1 and end > start:
                            parsed = json.loads(raw[start : end + 1])
                            if isinstance(parsed, dict) and parsed.get("decision") in (
                                "refresh_stock",
                                "ai_failover",
                                "open_issue",
                            ):
                                return {
                                    "decision": str(parsed["decision"]),
                                    "ticker": str(parsed.get("ticker") or rep.get("ticker") or "").strip(),
                                    "reason": str(parsed.get("reason") or "").strip(),
                                }
                    except Exception:
                        # Fallback to local heuristic rules if AI provider call fails
                        continue
        except Exception:
            # Skip AI agent evaluation if provider modules are unavailable
            return None
        return None

    def triage_feedback_reports(self) -> List[Dict[str, Any]]:
        """
        Evaluates user-reported faulty replies (capturing the last 3 bot replies) during self-patrol:
        1. Autonomous Agent / LLM + rule evaluation determines whether the issue is directly fixable or needs a GitHub Issue.
        2. Missing / stale / zero valuation data for a stock -> auto-refreshes ticker via Orchestrator.
        3. AI provider failure / timeout / truncation -> triggers AI health check & provider failover.
        4. Code / formatting / logic bugs -> deduplicates and opens a GitHub Issue.
        """
        pending = self.feedback_store.pending_reports()
        if not pending:
            return []

        processed: List[Dict[str, Any]] = []
        summary_lines = ["📋 <b>【GVW 自我巡檢】使用者問題回報整理與評估</b>"]

        for rep in pending:
            rep_id = rep.get("id", 0)
            bot_reply = str(rep.get("bot_reply", ""))
            raw_replies = rep.get("bot_replies")
            replies_list = (
                [str(r) for r in raw_replies if str(r).strip()]
                if isinstance(raw_replies, list) and raw_replies
                else ([bot_reply] if bot_reply else [])
            )[-3:]
            user_input = str(rep.get("user_input", ""))
            note = str(rep.get("note", ""))
            ticker = str(rep.get("ticker", ""))
            combined = f"{user_input}\n" + "\n".join(replies_list) + f"\n{note}"

            # 1. Autonomous evaluation via AI Agent if configured, with deterministic fallback
            agent_eval = self._evaluate_feedback_via_agent(rep, replies_list)
            decision = agent_eval.get("decision") if agent_eval else None
            eval_reason = agent_eval.get("reason") if agent_eval else ""
            if agent_eval and agent_eval.get("ticker") and not ticker:
                ticker = agent_eval["ticker"]

            if not decision:
                is_code_or_format_bug = any(
                    kw in combined for kw in ("**", "粗體", "語法", "排版", "格式", "按鈕", "Traceback")
                )
                is_data_issue = bool(ticker) and not is_code_or_format_bug and any(
                    kw in combined
                    for kw in (
                        "尚無已採集資料",
                        "本益比 (P/E)：0.0",
                        "股價淨值比 (P/B)：0",
                        "最新收盤：N/A",
                        "資料缺漏",
                        "缺漏",
                        "空白",
                        "無資料",
                    )
                )
                is_ai_issue = not is_code_or_format_bug and any(
                    kw in combined
                    for kw in (
                        "分析請求失敗",
                        "429",
                        "503",
                        "Peer count",
                        "Industry Avg",
                        "尚未設定",
                        "耗盡 Token",
                    )
                )
                if is_data_issue:
                    decision = "refresh_stock"
                elif is_ai_issue:
                    decision = "ai_failover"
                else:
                    decision = "open_issue"

            if decision == "refresh_stock" and ticker:
                try:
                    self.orchestrator.update_stock(ticker, force=True)
                    resolution = f"已自動重新強制採集 {ticker} 行情與財務估值資料"
                    if eval_reason:
                        resolution += f"（評估：{eval_reason}）"
                    self.feedback_store.mark_resolved(rep_id, "auto_fixed", resolution)
                    self._record(f"問題回報 #{rep_id} 自動修復：{resolution}")
                    summary_lines.append(f"• <b>回報 #{rep_id} ({ticker})</b>：🛠️ 可直接修復 ➔ {resolution}")
                    processed.append({"id": rep_id, "action": "auto_fixed", "detail": resolution})
                    continue
                except Exception as exc:
                    logger.warning("Auto-fix stock refresh failed for feedback #%s: %s", rep_id, exc)

            if decision == "ai_failover":
                try:
                    rem_actions = self.doctor.run_remediation(light=True)
                    real_rem = [a for a in rem_actions if "無須" not in a]
                    resolution = (
                        "；".join(real_rem)
                        if real_rem
                        else "已執行 AI 備援鏈路健康檢查與狀態重整"
                    )
                    if eval_reason:
                        resolution += f"（評估：{eval_reason}）"
                    self.feedback_store.mark_resolved(rep_id, "auto_fixed", resolution)
                    self._record(f"問題回報 #{rep_id} 自動修復：{resolution}")
                    summary_lines.append(f"• <b>回報 #{rep_id}</b>：🛠️ 可直接修復 ➔ {resolution}")
                    processed.append({"id": rep_id, "action": "auto_fixed", "detail": resolution})
                    continue
                except Exception as exc:
                    logger.warning("Auto-fix AI failover failed for feedback #%s: %s", rep_id, exc)

            # Case 3: Escalate to GitHub Issue for code/formatting/logic fix
            sig_seed = re.sub(r"\s+", " ", (note or user_input or bot_reply[:120])).strip()
            sig_hash = hashlib.sha1(sig_seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
            signature = f"user-feedback:{sig_hash}"
            title_snippet = (
                eval_reason
                or note
                or user_input
                or (bot_reply.splitlines()[0] if bot_reply else "異常回覆")
            )[:50]
            issue_title = f"使用者回報異常：{title_snippet}"
            replies_markdown = "\n\n".join(
                f"**回覆 #{idx}**\n```text\n{r[:1200]}\n```"
                for idx, r in enumerate(replies_list, start=1)
            )
            issue_body = (
                f"### 使用者問題回報 (#{rep_id})\n"
                f"- **回報時間**：`{rep.get('created_at')}`\n"
                f"- **使用者輸入**：`{user_input or '無'}`\n"
                f"- **補充說明**：`{note or '無'}`\n"
                f"- **相關個股**：`{ticker or '無'}`\n"
                + (f"- **自主巡檢評估**：`{eval_reason}`\n" if eval_reason else "")
                + f"\n#### 機器人最近 {len(replies_list)} 筆回覆內容\n"
                f"{replies_markdown}\n\n"
                "經自主巡檢機器人評估無法僅靠重抓資料或切換備援解決，建立 Issue 追蹤修復。"
            )
            try:
                created, url = self.doctor.report_unresolved_issue(
                    title=issue_title,
                    body=issue_body,
                    error_signature=signature,
                    labels=["bug", "user-feedback"],
                )
                act_label = "已建立新 Issue" if created else "已歸併至既有 Issue"
                resolution = f"{act_label}：{url}"
                self.feedback_store.mark_resolved(rep_id, "issue_opened", resolution)
                self._record(f"問題回報 #{rep_id} 轉開單：{resolution}")
                summary_lines.append(f"• <b>回報 #{rep_id}</b>：🧾 需改動程式碼，{act_label} ➔ {url}")
                processed.append({"id": rep_id, "action": "issue_opened", "detail": url})
            except Exception as exc:
                logger.warning("Failed to open GitHub issue for feedback #%s: %s", rep_id, exc)
                resolution = f"評估需開立 Issue（待 GitHub 連線恢復）：{issue_title}"
                self.feedback_store.mark_resolved(rep_id, "escalated_local", resolution)
                self._record(f"問題回報 #{rep_id}：{resolution}")
                summary_lines.append(f"• <b>回報 #{rep_id}</b>：⚠️ {resolution}")
                processed.append({"id": rep_id, "action": "escalated_local", "detail": resolution})

        if processed:
            self.notify_owner("\n".join(summary_lines))
        return processed

    def _escalate(self, signature: str, diag: Dict[str, Any]) -> None:
        body = (
            f"自主巡檢連續 {ESCALATE_AFTER} 次偵測到同一問題且自動修復無效：\n\n- {signature}\n\n"
            f"```json\n{json.dumps(diag, ensure_ascii=False, indent=2, default=str)[:3500]}\n```\n"
            "可用 `/fix #<編號>` 交由 Pi Agent 嘗試修復（產生 PR 供核准）。"
        )
        try:
            created, url = self.doctor.report_unresolved_issue(
                title=signature, body=body, error_signature=f"autopilot:{signature}",
                labels=["bug", "autopilot"],
            )
            self._record(f"已{'建立' if created else '沿用'} GitHub Issue：{url}")
            self.notify_owner(f"🧾 <b>問題持續未解，已{'建立' if created else '沿用'} Issue</b>\n{signature}\n{url}")
        except Exception as e:
            logger.warning(f"Escalation failed: {e}")

    def data_staleness(self) -> Dict[str, Any]:
        """Fraction of live tickers whose latest price date lags the freshest ticker."""
        health = self.state.get_health_status() or {}
        latest: Dict[str, str] = {}
        for ticker in self.state.list_stock_tickers():
            if health.get(ticker, {}).get("status") == "dead":
                continue
            prices = (self.state.get_stock_data(ticker) or {}).get("prices") or []
            dates = [str(p.get("date", ""))[:10] for p in prices if p.get("date")]
            if dates:
                latest[ticker] = max(dates)
        if not latest:
            return {"total": 0, "stale": 0, "ratio": 0.0, "reference": None}
        reference = max(latest.values())
        stale = [t for t, d in latest.items() if d < reference]
        return {"total": len(latest), "stale": len(stale), "ratio": len(stale) / len(latest), "reference": reference}

    def premarket_check(self) -> Dict[str, Any]:
        """Before the open: make sure data is as fresh/correct as possible, rerank if we fixed it."""
        before = self.data_staleness()
        prev_weekday = datetime.now().date() - timedelta(days=1)
        while prev_weekday.weekday() >= 5:
            prev_weekday -= timedelta(days=1)
        market_lagging = bool(before["reference"]) and before["reference"] < prev_weekday.isoformat()
        result = {"before": before, "refreshed": False, "after": before}
        if before["ratio"] > STALE_RATIO_THRESHOLD or market_lagging:
            self._record(f"盤前資料落後 {before['stale']}/{before['total']}，啟動補採集")
            self.orchestrator.run_collection_cycle(batch_ratio=0.5, force=False)
            self.ranking_fn()
            result["after"] = self.data_staleness()
            result["refreshed"] = True
            self._record(f"補採集後落後 {result['after']['stale']}/{result['after']['total']}")
            if result["after"]["ratio"] > STALE_RATIO_THRESHOLD:
                self.notify_owner(
                    "⚠️ <b>【盤前資料檢查】</b>補採集後仍有 "
                    f"{result['after']['stale']}/{result['after']['total']} 檔資料落後（參考日 {result['after']['reference']}）。"
                    "可能是資料源延遲或休市，建議開盤前留意。"
                )
        else:
            self._record(f"盤前資料新鮮（落後比例 {before['ratio']:.1%}）")
        return result

    def postmarket_review(self) -> Dict[str, Any]:
        """After ranking: log picks, score past picks, maybe propose tuning, send one digest."""
        self.triage_feedback_reports()
        self.benchmark.update(self.state.get_market_index())
        recorded = self.ledger.record(self.state.get_ranks())
        filled = self.ledger.evaluate(self.benchmark)
        summary = self.ledger.summary()
        self._record(f"回測：記錄今日 {recorded} 檔推薦，新增 {filled} 筆已實現報酬")
        proposal = None
        if datetime.now().weekday() == 4:  # Friday: weekly tuning proposal
            proposal = self.ledger.propose_weights()
            if proposal:
                self._save_proposal(proposal)
                self.notify_owner(self._format_proposal(proposal), reply_markup={"inline_keyboard": [[
                    {"text": "✅ 套用新權重", "callback_data": "tune:apply"},
                    {"text": "🚫 忽略", "callback_data": "tune:ignore"},
                ]]})
        self.send_digest(summary, proposal)
        return {"recorded": recorded, "filled": filled, "summary": summary, "proposal": proposal}

    def nightly_maintenance(self) -> List[str]:
        self.triage_feedback_reports()
        actions = self.doctor.run_remediation(light=False)
        real = [a for a in actions if "無須" not in a]
        if real:
            self._record("夜間維護：" + "；".join(real))
        return actions

    # ---------- digest ----------
    def send_digest(self, summary: Dict[str, Any], proposal: Optional[Dict[str, Any]] = None) -> None:
        lines = ["🩺 <b>【GVW 每日自主管理摘要】</b>", ""]
        h = summary.get("horizons", {})
        measured = [(k, v) for k, v in h.items() if v["n"]]
        if measured:
            lines.append("📊 <b>歷史推薦績效（Top 50 實測）</b>")
            for k, v in measured:
                line = f"• T+{k}：勝率 <code>{v['win_rate']:.0%}</code>，平均報酬 <code>{v['avg_return']:+.2%}</code>（n={v['n']}）"
                if v.get("n_excess"):
                    line += f"；相對大盤 <code>{v['avg_excess']:+.2%}</code>，贏大盤 <code>{v['beat_rate']:.0%}</code>"
                lines.append(line)
        else:
            lines.append("📊 回測資料累積中（需數個交易日才有 T+1/T+5 結果）")
        lines += ["", "🛠️ <b>今日自主處理紀錄</b>"]
        lines += [f"• {e}" for e in self.events[-12:]] or ["• 一切正常，無需處理 ✅"]
        if proposal:
            lines += ["", "🧪 本週已提出權重調整建議（見上則訊息，待您核准）"]
        self.events.clear()
        self.notify_owner("\n".join(lines))

    # ---------- tuning proposal ----------
    @property
    def _proposal_path(self) -> str:
        return os.path.join(self.data_dir, "tuning_proposal.json")

    @property
    def _weights_path(self) -> str:
        return os.path.join(self.data_dir, "tuning_weights.json")

    def _save_proposal(self, proposal: Dict[str, Any]) -> None:
        with open(self._proposal_path, "w", encoding="utf-8") as f:
            json.dump(proposal, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _format_proposal(p: Dict[str, Any]) -> str:
        names = {"momentum": "動能", "trend": "趨勢", "quality": "品質"}
        rows = [
            f"• {names[k]}：{p['current'][k]:.0%} → <b>{p['proposed'][k]:.0%}</b>（與 T+5 報酬相關 {p['correlation'][k]:+.2f}）"
            for k in names
        ]
        return (
            "🧪 <b>【每週選股權重調整建議】</b>\n"
            f"依據 {p['samples']} 筆已實現 T+5 結果：\n" + "\n".join(rows) +
            "\n\n⚠️ 樣本仍短，核准後可隨時再調回。"
        )

    def apply_proposal(self) -> Optional[Dict[str, float]]:
        try:
            with open(self._proposal_path, "r", encoding="utf-8") as f:
                proposal = json.load(f)
        except Exception:
            return None
        with open(self._weights_path, "w", encoding="utf-8") as f:
            json.dump(proposal["proposed"], f, indent=2)
        os.remove(self._proposal_path)
        self._record(f"已套用新權重：{proposal['proposed']}")
        return proposal["proposed"]

    def dismiss_proposal(self) -> bool:
        if os.path.exists(self._proposal_path):
            os.remove(self._proposal_path)
            return True
        return False
