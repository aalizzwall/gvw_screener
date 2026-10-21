import os
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock

from src.diagnostics.doctor import GVWDoctor
from src.agent.issue_solver import IssueSolver, SecurityGuardrailError
from gvw_bot.bot_handler import TelegramBotHandler
from src.state.ai_provider import AIProviderPreferenceStore


class TestGVWDoctor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.temp_dir.name, "data")
        self.logs_dir = os.path.join(self.temp_dir.name, "logs")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)
        self.store = AIProviderPreferenceStore(os.path.join(self.data_dir, "ai_pref.json"))
        self.doctor = GVWDoctor(
            data_dir=self.data_dir,
            logs_dir=self.logs_dir,
            ai_provider_store=self.store,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_memory_metrics(self):
        metrics = self.doctor.get_memory_metrics()
        self.assertIn("total_mb", metrics)
        self.assertIn("used_mb", metrics)
        self.assertIn("percent", metrics)

    def test_get_disk_metrics(self):
        metrics = self.doctor.get_disk_metrics()
        self.assertIn("total_gb", metrics)
        self.assertIn("free_gb", metrics)
        self.assertIn("percent", metrics)

    def test_get_cpu_temperature_mock(self):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data="46800\n")):
            temp = self.doctor.get_cpu_temperature()
            self.assertEqual(temp, 46.8)

    def test_get_heartbeat_status(self):
        # File doesn't exist yet
        hb = self.doctor.get_heartbeat_status()
        self.assertFalse(hb["exists"])

        # Create fresh heartbeat
        hb_path = os.path.join(self.data_dir, "heartbeat.json")
        with open(hb_path, "w") as f:
            json.dump({"timestamp": "2026-10-21T21:00:00", "status": "running"}, f)

        with patch("src.diagnostics.doctor.datetime") as mock_dt:
            from datetime import datetime
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.now.return_value = datetime.fromisoformat("2026-10-21T21:01:00")
            hb = self.doctor.get_heartbeat_status()
            self.assertTrue(hb["exists"])
            self.assertTrue(hb["fresh"])
            self.assertEqual(hb["age_seconds"], 60)

    def test_get_database_status(self):
        db_path = os.path.join(self.data_dir, "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test (id INT);")
        conn.commit()
        conn.close()

        status = self.doctor.get_database_status()
        self.assertTrue(status["all_ok"])
        self.assertIn("test.db", status["databases"])
        self.assertTrue(status["databases"]["test.db"]["ok"])

    def test_format_diagnostics_report(self):
        mock_diag = {
            "timestamp": "2026-10-21T21:00:00",
            "memory": {"total_mb": 1024, "used_mb": 512, "free_mb": 512, "percent": 50.0},
            "disk": {"total_gb": 32.0, "used_gb": 10.0, "free_gb": 22.0, "percent": 31.2},
            "cpu_temp": 48.2,
            "heartbeat": {"exists": True, "status": "running", "fresh": True, "age_seconds": 15},
            "systemd": "active",
            "database": {"all_ok": True, "databases": {}, "total_size_mb": 4.5},
            "connectivity": {
                "telegram": {"ok": True, "latency_ms": 110},
                "twse": {"ok": True, "latency_ms": 190},
                "ai": {"primary": "openai/gpt-4o-mini", "primary_ok": True},
            },
        }
        report = self.doctor.format_diagnostics_report(mock_diag)
        self.assertIn("GVW 系統全方位健康診斷報告", report)
        self.assertIn("48.2°C", report)
        self.assertIn("openai/gpt-4o-mini", report)
        self.assertIn("PRAGMA OK", report)

    def test_run_remediation_actions(self):
        # 1. Create a large log file
        log_file = os.path.join(self.logs_dir, "gvw_bot.log")
        with open(log_file, "w") as f:
            for i in range(5000):
                f.write(f"Line {i}\n")
        # Artificially mock getsize to > 10MB
        with patch("os.path.getsize", return_value=12 * 1024 * 1024), \
             patch("src.diagnostics.doctor.check_providers", return_value=[{"entry": "openai", "ok": True}]):
            actions = self.doctor.run_remediation()
            self.assertTrue(any("輪替過大日誌" in act for act in actions))

        # 2. Test stale lock cleanup
        lock_file = os.path.join(self.data_dir, "old.lock")
        with open(lock_file, "w") as f:
            f.write("lock")
        # Set mtime to 2 hours ago
        os.utime(lock_file, (time.time() - 7200, time.time() - 7200))

        with patch("src.diagnostics.doctor.check_providers", return_value=[]):
            actions = self.doctor.run_remediation()
            self.assertTrue(any("清理" in act and "鎖檔" in act for act in actions))
            self.assertFalse(os.path.exists(lock_file))

    def test_report_unresolved_issue(self):
        # 1. Already exists
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=json.dumps([{"number": 28, "url": "https://github.com/test/28"}]),
            )
            created, url = self.doctor.report_unresolved_issue("TWSE error", "body", "ERR_SIG_123")
            self.assertFalse(created)
            self.assertEqual(url, "https://github.com/test/28")

        # 2. Create new
        with patch("subprocess.run") as mock_run:
            # First call: search returns empty list
            # Second call: gh issue create returns new URL
            mock_run.side_effect = [
                Mock(returncode=0, stdout="[]"),
                Mock(returncode=0, stdout="https://github.com/test/29\n"),
            ]
            created, url = self.doctor.report_unresolved_issue("New Bug", "details", "ERR_SIG_999")
            self.assertTrue(created)
            self.assertEqual(url, "https://github.com/test/29")


class TestIssueSolver(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.solver = IssueSolver(repo_root=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_validate_file_paths_allowed(self):
        valid_files = [
            {"path": "src/data/twse_client.py", "content": "# code"},
            {"path": "src/analysis/screener.py", "content": "# code"},
            {"path": "tests/test_screener.py", "content": "# code"},
        ]
        # Should not raise
        self.solver.validate_file_paths(valid_files)

    def test_validate_file_paths_rejects_forbidden_and_too_many(self):
        # 1. Forbidden files
        forbidden_cases = [
            [{"path": ".env", "content": "KEY=val"}],
            [{"path": "gvw_bot/main.py", "content": "# code"}],
            [{"path": "requirements.txt", "content": "pkg"}],
            [{"path": ".github/workflows/deploy.yml", "content": "yml"}],
            [{"path": "src/agent/issue_solver.py", "content": "# bypass"}],
            [{"path": "src/data/../../etc/passwd", "content": "hack"}],
        ]
        for case in forbidden_cases:
            with self.assertRaises(SecurityGuardrailError):
                self.solver.validate_file_paths(case)

        # 2. Too many files (>3)
        too_many = [
            {"path": f"src/data/file{i}.py", "content": "c"} for i in range(4)
        ]
        with self.assertRaises(SecurityGuardrailError):
            self.solver.validate_file_paths(too_many)

    def test_validate_diff_anti_cheat(self):
        # Mocks git diff tests/ deleting assertions
        with patch("subprocess.check_output") as mock_diff:
            mock_diff.side_effect = [
                "10\t5\tsrc/data/twse_client.py\n",  # git diff --numstat
                "--- a/tests/test_foo.py\n+++ b/tests/test_foo.py\n-        self.assertEqual(a, b)\n+        pass\n",  # git diff tests/
            ]
            with self.assertRaises(SecurityGuardrailError) as ctx:
                self.solver.validate_diff_and_anti_cheat("/tmp/sandbox")
            self.assertIn("作弊行為", str(ctx.exception))

    def test_validate_diff_rejects_excessive_lines(self):
        with patch("subprocess.check_output") as mock_diff:
            mock_diff.return_value = "120\t50\tsrc/data/twse_client.py\n"  # 170 lines total > 150
            with self.assertRaises(SecurityGuardrailError) as ctx:
                self.solver.validate_diff_and_anti_cheat("/tmp/sandbox")
            self.assertIn("超出允許上限", str(ctx.exception))

    def test_solve_issue_happy_path(self):
        mock_issue_data = {
            "number": 28,
            "title": "TWSE parse bug",
            "body": "Error in src/data/twse_client.py",
            "state": "OPEN",
        }
        mock_llm_json = json.dumps({
            "explanation": "Fixed TWSE parse key error",
            "files": [
                {"path": "src/data/twse_client.py", "content": "# fixed twse code\n"}
            ],
        })

        with patch.object(self.solver, "fetch_issue", return_value=mock_issue_data), \
             patch.object(self.solver, "_call_llm", return_value=mock_llm_json), \
             patch("subprocess.run") as mock_run, \
             patch("subprocess.check_output", return_value="5\t2\tsrc/data/twse_client.py\n"):

            # All subprocess runs (git worktree, tests, commit, push, pr) succeed
            mock_run.return_value = Mock(returncode=0, stdout="https://github.com/test/pull/29\n", stderr="")

            res = self.solver.solve_issue(28)

            self.assertTrue(res["success"])
            self.assertEqual(res["issue_id"], "28")
            self.assertEqual(res["pr_url"], "https://github.com/test/pull/29")
            self.assertIn("src/data/twse_client.py", res["modified_files"])

    def test_solve_issue_closed_early_return(self):
        mock_issue_data = {
            "number": 28,
            "title": "Old bug",
            "body": "Fixed already",
            "state": "CLOSED",
        }
        with patch.object(self.solver, "fetch_issue", return_value=mock_issue_data):
            res = self.solver.solve_issue(28)
            self.assertFalse(res["success"])
            self.assertIn("已經是關閉狀態", res["message"])


class TestTelegramBotDoctorCommands(unittest.TestCase):
    def setUp(self):
        self.bot = TelegramBotHandler(token="test-token", owner_user_id="10")
        self.bot.send_message = Mock()

    def test_non_owner_cannot_run_doctor_or_fix(self):
        self.bot.handle_command(20, "/doctor", "/doctor", user_id=20)
        self.assertIn("僅限系統負責人", self.bot.send_message.call_args.args[1])

        self.bot.handle_command(20, "/fix", "/fix #28", user_id=20)
        self.assertIn("僅限系統負責人", self.bot.send_message.call_args.args[1])

    def test_owner_runs_doctor_diagnostics(self):
        with patch.object(self.bot.doctor, "format_diagnostics_report", return_value="<b>健康報告</b>"):
            self.bot.handle_command(10, "/doctor", "/doctor", user_id=10)
            calls = [c.args[1] for c in self.bot.send_message.call_args_list]
            self.assertTrue(any("健康報告" in c for c in calls))

    def test_owner_runs_doctor_fix_routine(self):
        with patch.object(self.bot.doctor, "run_remediation", return_value=["已壓縮 SQLite"]):
            self.bot.handle_command(10, "/doctor", "/doctor fix", user_id=10)
            calls = [c.args[1] for c in self.bot.send_message.call_args_list]
            self.assertTrue(any("已壓縮 SQLite" in c for c in calls))

    def test_owner_runs_fix_issue_dispatches_thread(self):
        with patch.object(self.bot.issue_solver, "solve_issue") as mock_solve:
            mock_solve.return_value = {
                "success": True,
                "title": "Fix bug",
                "modified_files": ["src/data/twse_client.py"],
                "explanation": "Fixed bug",
                "pr_url": "https://github.com/test/pull/30",
            }

            self.bot.handle_command(10, "/fix", "/fix #28", user_id=10)

            # Give daemon thread a brief moment to execute
            time.sleep(0.1)

            calls = [c.args[1] for c in self.bot.send_message.call_args_list]
            self.assertTrue(any("自主修復任務已啟動" in c for c in calls))
            self.assertTrue(any("已建立 Pull Request" in c for c in calls))


if __name__ == "__main__":
    unittest.main()

