import os
import sys
import glob
import json
import time
import shutil
import sqlite3
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import requests

from src.config import DATA_DIR, LOGS_DIR
from src.state.ai_provider import AIProviderPreferenceStore
from src.analysis.ai_analyst import check_providers

logger = logging.getLogger("gvw.doctor")


class GVWDoctor:
    """
    Performs comprehensive system diagnostics, safe routine remediation,
    and automatic GitHub issue creation for unresolvable bugs.
    """

    def __init__(
        self,
        data_dir: str = DATA_DIR,
        logs_dir: str = LOGS_DIR,
        ai_provider_store: Optional[AIProviderPreferenceStore] = None,
    ):
        self.data_dir = data_dir
        self.logs_dir = logs_dir
        self.ai_provider_store = ai_provider_store or AIProviderPreferenceStore()

    # ---------------------------------------------------------
    # 1. System & Hardware Metrics
    # ---------------------------------------------------------
    def get_memory_metrics(self) -> Dict[str, Any]:
        """Returns memory metrics in MB and percentage."""
        # Try /proc/meminfo first (Linux / Raspberry Pi)
        if os.path.exists("/proc/meminfo"):
            try:
                meminfo = {}
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            key = parts[0].strip()
                            val = parts[1].strip().split()[0]
                            meminfo[key] = int(val)
                total_kb = meminfo.get("MemTotal", 0)
                avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
                used_kb = max(0, total_kb - avail_kb)
                total_mb = round(total_kb / 1024, 1)
                used_mb = round(used_kb / 1024, 1)
                free_mb = round(avail_kb / 1024, 1)
                pct = round((used_kb / total_kb * 100), 1) if total_kb > 0 else 0.0
                return {"total_mb": total_mb, "used_mb": used_mb, "free_mb": free_mb, "percent": pct}
            except Exception as e:
                logger.debug(f"Error parsing /proc/meminfo: {e}")

        # Fallback to sysconf (Unix / macOS)
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            total_mb = round((pages * page_size) / (1024 * 1024), 1)
            return {"total_mb": total_mb, "used_mb": 0.0, "free_mb": total_mb, "percent": 0.0}
        except Exception:
            return {"total_mb": 0.0, "used_mb": 0.0, "free_mb": 0.0, "percent": 0.0}

    def get_disk_metrics(self) -> Dict[str, Any]:
        """Returns disk space usage for data directory."""
        try:
            target_path = self.data_dir if os.path.exists(self.data_dir) else "/"
            total, used, free = shutil.disk_usage(target_path)
            total_gb = round(total / (1024**3), 1)
            used_gb = round(used / (1024**3), 1)
            free_gb = round(free / (1024**3), 1)
            pct = round((used / total * 100), 1) if total > 0 else 0.0
            return {"total_gb": total_gb, "used_gb": used_gb, "free_gb": free_gb, "percent": pct}
        except Exception as e:
            logger.debug(f"Error reading disk usage: {e}")
            return {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0}

    def get_cpu_temperature(self) -> Optional[float]:
        """Returns CPU temperature in Celsius (Raspberry Pi specific)."""
        temp_file = "/sys/class/thermal/thermal_zone0/temp"
        if os.path.exists(temp_file):
            try:
                with open(temp_file, "r") as f:
                    temp_raw = f.read().strip()
                return round(float(temp_raw) / 1000.0, 1)
            except Exception:
                pass

        # Try vcgencmd if available
        try:
            res = subprocess.check_output(["vcgencmd", "measure_temp"], text=True, timeout=2).strip()
            # Format: temp=45.6'C
            if "temp=" in res:
                temp_str = res.split("temp=")[1].split("'")[0]
                return float(temp_str)
        except Exception:
            pass

        return None

    # ---------------------------------------------------------
    # 2. Service & Heartbeat Diagnostics
    # ---------------------------------------------------------
    def get_heartbeat_status(self) -> Dict[str, Any]:
        """Checks heartbeat file freshness and service state."""
        hb_path = os.path.join(self.data_dir, "heartbeat.json")
        if not os.path.exists(hb_path):
            return {"exists": False, "status": "unknown", "fresh": False, "age_seconds": None}

        try:
            with open(hb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ts = data.get("timestamp")
            status = data.get("status", "unknown")
            age = None
            fresh = False
            if ts:
                # ISO timestamp
                dt = datetime.fromisoformat(ts)
                age = max(0, int((datetime.now() - dt).total_seconds()))
                # Fresh if updated within 5 minutes
                fresh = age <= 300
            return {
                "exists": True,
                "status": status,
                "fresh": fresh,
                "age_seconds": age,
                "last_run": data.get("last_run", {}),
            }
        except Exception as e:
            return {"exists": True, "status": f"read_error: {e}", "fresh": False, "age_seconds": None}

    def get_systemd_status(self) -> str:
        """Returns systemd service active status."""
        try:
            res = subprocess.run(
                ["systemctl", "--user", "is-active", "gvw_bot"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return res.stdout.strip() or res.stderr.strip() or "inactive"
        except Exception:
            return "n/a"

    # ---------------------------------------------------------
    # 3. Database Integrity Diagnostics
    # ---------------------------------------------------------
    def get_database_status(self) -> Dict[str, Any]:
        """Inspects SQLite databases for integrity and file sizes."""
        db_files = glob.glob(os.path.join(self.data_dir, "*.db"))
        results = {}
        all_ok = True
        total_bytes = 0

        for db in db_files:
            name = os.path.basename(db)
            size = os.path.getsize(db)
            total_bytes += size
            try:
                conn = sqlite3.connect(db, timeout=5)
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                check_result = cursor.fetchone()[0]
                conn.close()
                is_ok = (check_result.lower() == "ok")
                if not is_ok:
                    all_ok = False
                results[name] = {"integrity": check_result, "ok": is_ok, "size_mb": round(size / (1024 * 1024), 2)}
            except Exception as e:
                all_ok = False
                results[name] = {"integrity": str(e), "ok": False, "size_mb": round(size / (1024 * 1024), 2)}

        return {
            "all_ok": all_ok,
            "databases": results,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        }

    # ---------------------------------------------------------
    # 4. External Connectivity Diagnostics
    # ---------------------------------------------------------
    def check_external_connectivity(self) -> Dict[str, Any]:
        """Tests connectivity to Telegram API, TWSE, and Active AI Provider."""
        results = {}

        # 1. Telegram API
        try:
            t0 = time.time()
            resp = requests.get("https://api.telegram.org", timeout=5)
            ms = int((time.time() - t0) * 1000)
            results["telegram"] = {"ok": resp.status_code < 500, "latency_ms": ms, "status": resp.status_code}
        except Exception as e:
            results["telegram"] = {"ok": False, "error": str(e)}

        # 2. TWSE Website
        try:
            t0 = time.time()
            resp = requests.get("https://www.twse.com.tw", timeout=5)
            ms = int((time.time() - t0) * 1000)
            results["twse"] = {"ok": resp.status_code == 200, "latency_ms": ms, "status": resp.status_code}
        except Exception as e:
            results["twse"] = {"ok": False, "error": str(e)}

        # 3. AI Providers
        try:
            chain = self.ai_provider_store.get_chain()
            primary = chain[0] if chain else "none"
            probe_results = check_providers(chain)
            status_map = {res["entry"]: res for res in probe_results}
            primary_ok = status_map.get(primary, {}).get("ok", False)
            results["ai"] = {
                "primary": primary,
                "chain": chain,
                "details": status_map,
                "primary_ok": primary_ok,
            }
        except Exception as e:
            results["ai"] = {"ok": False, "error": str(e)}

        return results

    # ---------------------------------------------------------
    # 5. Full Report Generation
    # ---------------------------------------------------------
    def run_diagnostics(self) -> Dict[str, Any]:
        """Runs all diagnostics and returns full report dict."""
        return {
            "timestamp": datetime.now().isoformat(),
            "memory": self.get_memory_metrics(),
            "disk": self.get_disk_metrics(),
            "cpu_temp": self.get_cpu_temperature(),
            "heartbeat": self.get_heartbeat_status(),
            "systemd": self.get_systemd_status(),
            "database": self.get_database_status(),
            "connectivity": self.check_external_connectivity(),
        }

    def format_diagnostics_report(self, diag: Optional[Dict[str, Any]] = None) -> str:
        """Formats diagnostics dictionary into readable Telegram HTML message."""
        d = diag or self.run_diagnostics()

        mem = d["memory"]
        disk = d["disk"]
        cpu_temp = d["cpu_temp"]
        hb = d["heartbeat"]
        sysd = d["systemd"]
        db = d["database"]
        conn = d["connectivity"]

        # CPU Temp display
        if cpu_temp is not None:
            temp_tag = "🟢 正常" if cpu_temp < 65 else ("🟡 偏熱" if cpu_temp < 75 else "🔴 過熱警報")
            temp_str = f"• CPU 溫度：<code>{cpu_temp}°C</code> ({temp_tag})\n"
        else:
            temp_str = ""

        # Heartbeat display
        if hb["exists"]:
            age_str = f"{hb['age_seconds']} 秒前" if hb["age_seconds"] is not None else "未知"
            hb_tag = "✅" if hb["fresh"] else "⚠️ 過久未更新"
            hb_line = f"• 心跳檢測：<code>{hb['status']} ({age_str})</code> {hb_tag}"
        else:
            hb_line = "• 心跳檢測：<code>無心跳記錄 ⚠️</code>"

        # Service display
        sysd_tag = "✅" if sysd == "active" else ("⚪" if sysd == "n/a" else "⚠️")

        # Connectivity
        tg = conn.get("telegram", {})
        tg_str = f"正常 ({tg.get('latency_ms', 0)}ms) ✅" if tg.get("ok") else "異常 ❌"

        twse = conn.get("twse", {})
        twse_str = f"正常 ({twse.get('latency_ms', 0)}ms) ✅" if twse.get("ok") else "異常 ❌"

        ai = conn.get("ai", {})
        ai_pri = ai.get("primary", "未設定")
        ai_ok = ai.get("primary_ok", False)
        ai_str = f"<code>{ai_pri}</code> ({'可用 ✅' if ai_ok else '異常 ❌'})"

        # DB status
        db_tag = "✅ PRAGMA OK" if db.get("all_ok", True) else "❌ 發現損毀"
        db_size = db.get("total_size_mb", 0)

        lines = [
            "🩺 <b>GVW 系統全方位健康診斷報告</b>",
            "",
            "💻 <b>硬體資源：</b>",
            f"• 記憶體：<code>{mem['used_mb']}MB / {mem['total_mb']}MB ({mem['percent']}%)</code>",
            f"• 磁碟空間：<code>{disk['free_gb']}GB 可用 / {disk['total_gb']}GB ({disk['percent']}% 已用)</code>",
            temp_str.strip() if temp_str else "",
            "",
            "🤖 <b>守護行程與排程：</b>",
            f"• 系統服務：<code>{sysd}</code> {sysd_tag}",
            hb_line,
            "",
            "🌐 <b>外部網路與 API：</b>",
            f"• Telegram API：<code>{tg_str}</code>",
            f"• TWSE 證交所：<code>{twse_str}</code>",
            f"• 主力 AI 模型：{ai_str}",
            "",
            "🗄️ <b>資料庫健康：</b>",
            f"• SQLite 完整性：<code>{db_tag}</code> (共 {db_size} MB)",
        ]
        return "\n".join([line for line in lines if line is not None])

    # ---------------------------------------------------------
    # 6. Safe Routine Remediation (Self-Healing)
    # ---------------------------------------------------------
    def run_remediation(self) -> List[str]:
        """
        Executes safe self-healing actions:
        - Rotates oversized logs
        - Clears stale temporary / lock files
        - VACUUM compresses SQLite databases
        - Automatically fails over AI provider if primary is down
        """
        actions = []

        # 1. Log Rotation: If gvw_bot.log > 10MB, keep last 2000 lines
        try:
            log_file = os.path.join(self.logs_dir, "gvw_bot.log")
            if os.path.exists(log_file) and os.path.getsize(log_file) > 10 * 1024 * 1024:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                trimmed = lines[-2000:]
                with open(log_file, "w", encoding="utf-8") as f:
                    f.writelines(trimmed)
                actions.append("已壓縮並輪替過大日誌 (保留最新 2000 行)")
        except Exception as e:
            logger.warning(f"Remediation log rotation failed: {e}")

        # 2. Clear stale lock files in data directory older than 1 hour
        try:
            now = time.time()
            locks = glob.glob(os.path.join(self.data_dir, "*.lock"))
            cleared = 0
            for lk in locks:
                if now - os.path.getmtime(lk) > 3600:
                    os.remove(lk)
                    cleared += 1
            if cleared > 0:
                actions.append(f"已清理 {cleared} 個殘留鎖檔 (*.lock)")
        except Exception as e:
            logger.warning(f"Remediation lock cleanup failed: {e}")

        # 3. VACUUM SQLite databases
        try:
            db_files = glob.glob(os.path.join(self.data_dir, "*.db"))
            vacuumed = 0
            for db in db_files:
                conn = sqlite3.connect(db, timeout=10)
                conn.execute("VACUUM;")
                conn.close()
                vacuumed += 1
            if vacuumed > 0:
                actions.append(f"已執行 SQLite VACUUM 最佳化 ({vacuumed} 個資料庫)")
        except Exception as e:
            logger.warning(f"Remediation SQLite vacuum failed: {e}")

        # 4. Auto AI Failover
        try:
            chain = self.ai_provider_store.get_chain()
            if chain:
                primary = chain[0]
                probe_results = check_providers(chain)
                status_map = {res["entry"]: res for res in probe_results}
                primary_ok = status_map.get(primary, {}).get("ok", False)
                if not primary_ok:
                    # Find first healthy entry
                    healthy = [res["entry"] for res in probe_results if res.get("ok")]
                    if healthy:
                        new_primary = healthy[0]
                        new_chain = healthy + [entry for entry in chain if entry not in healthy]
                        self.ai_provider_store.set_chain(new_chain)
                        actions.append(f"主力 AI ({primary}) 不可用，已自動切換備援主力：<code>{new_primary}</code>")
        except Exception as e:
            logger.warning(f"Remediation AI failover failed: {e}")

        if not actions:
            actions.append("各項指標與組態均良好，無須執行修復。")

        return actions

    # ---------------------------------------------------------
    # 7. Auto GitHub Issue Reporting
    # ---------------------------------------------------------
    def report_unresolved_issue(
        self,
        title: str,
        body: str,
        error_signature: str,
        labels: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """
        Deduplicates and creates a GitHub issue if not already open.
        Returns (created_new: bool, issue_url: str).
        """
        labels = labels or ["bug", "doctor-reported"]
        label_str = ",".join(labels)

        try:
            # 1. Search existing open issues with signature
            search_res = subprocess.run(
                ["gh", "issue", "list", "--state", "open", "--search", error_signature, "--json", "number,url"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if search_res.returncode == 0:
                existing = json.loads(search_res.stdout.strip() or "[]")
                if existing and isinstance(existing, list) and len(existing) > 0:
                    return False, existing[0].get("url", "")

            # 2. Create new issue
            formatted_body = (
                f"{body}\n\n"
                f"---\n"
                f"**Doctor Error Signature**: `{error_signature}`\n"
                f"**Reported At**: `{datetime.now().isoformat()}`\n"
            )
            create_res = subprocess.run(
                ["gh", "issue", "create", "--title", f"[Auto-Doctor] {title}", "--body", formatted_body, "--label", label_str],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if create_res.returncode == 0:
                new_url = create_res.stdout.strip()
                return True, new_url
            else:
                raise RuntimeError(create_res.stderr.strip() or "Failed to create issue via gh")

        except Exception as e:
            logger.error(f"Error creating GitHub issue: {e}")
            raise

