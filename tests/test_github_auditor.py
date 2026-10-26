import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from src.diagnostics.github_auditor import GitHubAuditor
from src.agent.maintenance_agent import MaintenanceAgent


class TestGitHubAuditor(unittest.TestCase):
    def setUp(self):
        self.auditor = GitHubAuditor(repo="test-owner/test-repo")

    def test_repo_detection_fallback(self):
        with patch("subprocess.run", side_effect=OSError("git not found")):
            auditor = GitHubAuditor(repo=None)
            self.assertEqual(auditor.repo, "aalizzwall/gvw_screener")

    def test_check_code_scanning_alerts(self):
        mock_output = json.dumps([
            {
                "number": 42,
                "rule": {
                    "id": "py/clear-text-logging",
                    "security_severity_level": "high",
                    "description": "Sensitive log",
                },
                "most_recent_instance": {
                    "location": {
                        "path": "src/test.py",
                        "start_line": 10,
                    }
                },
                "html_url": "https://github.com/test/security/42",
            }
        ])
        mock_res = Mock(returncode=0, stdout=mock_output)

        with patch("subprocess.run", return_value=mock_res) as mock_run:
            alerts = self.auditor.check_code_scanning_alerts()
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["number"], 42)
            self.assertEqual(alerts[0]["rule_id"], "py/clear-text-logging")
            self.assertEqual(alerts[0]["severity"], "high")
            self.assertEqual(alerts[0]["path"], "src/test.py")
            mock_run.assert_called_once()

    def test_check_workflow_failures(self):
        mock_output = json.dumps([
            {
                "name": "CI",
                "conclusion": "failure",
                "status": "completed",
                "url": "https://github.com/test/actions/1",
                "headBranch": "main",
                "createdAt": "2026-10-26T12:00:00Z",
                "event": "push",
            },
            {
                "name": "CodeQL",
                "conclusion": "success",
                "status": "completed",
                "url": "https://github.com/test/actions/2",
                "headBranch": "main",
                "createdAt": "2026-10-26T12:00:00Z",
                "event": "push",
            },
        ])
        mock_res = Mock(returncode=0, stdout=mock_output)

        with patch("subprocess.run", return_value=mock_res):
            failures = self.auditor.check_workflow_failures(days=7)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["name"], "CI")
            self.assertEqual(failures[0]["branch"], "main")

    def test_check_open_issues(self):
        mock_output = json.dumps([
            {
                "number": 10,
                "title": "Bug in calculation",
                "labels": [{"name": "bug"}],
                "createdAt": "2026-10-20T10:00:00Z",
                "updatedAt": "2026-10-22T10:00:00Z",
                "url": "https://github.com/test/issues/10",
            }
        ])
        mock_res = Mock(returncode=0, stdout=mock_output)

        with patch("subprocess.run", return_value=mock_res):
            issues = self.auditor.check_open_issues()
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["number"], 10)
            self.assertEqual(issues[0]["labels"], ["bug"])

    def test_run_full_audit_has_problems(self):
        with (
            patch.object(self.auditor, "check_code_scanning_alerts", return_value=[{"rule_id": "test"}]),
            patch.object(self.auditor, "check_workflow_failures", return_value=[]),
            patch.object(self.auditor, "check_open_issues", return_value=[]),
        ):
            res = self.auditor.run_full_audit()
            self.assertTrue(res["has_problems"])
            self.assertEqual(res["summary"]["alerts_count"], 1)
            self.assertEqual(res["summary"]["failures_count"], 0)

    def test_run_full_audit_clean(self):
        with (
            patch.object(self.auditor, "check_code_scanning_alerts", return_value=[]),
            patch.object(self.auditor, "check_workflow_failures", return_value=[]),
            patch.object(self.auditor, "check_open_issues", return_value=[]),
        ):
            res = self.auditor.run_full_audit()
            self.assertFalse(res["has_problems"])
            self.assertEqual(res["summary"]["alerts_count"], 0)

    def test_format_issue_content(self):
        audit_res = {
            "week_tag": "2026-W43",
            "timestamp": "2026-10-26T20:00:00",
            "summary": {"alerts_count": 1, "failures_count": 1, "open_issues_count": 1},
            "alerts": [
                {
                    "number": 5,
                    "rule_id": "py/empty-except",
                    "severity": "low",
                    "path": "src/cli.py",
                    "line": 20,
                    "url": "https://github.com/alert/5",
                }
            ],
            "workflow_failures": [
                {
                    "name": "CI",
                    "branch": "main",
                    "event": "push",
                    "url": "https://github.com/run/1",
                }
            ],
            "open_issues": [
                {
                    "number": 12,
                    "title": "Optimize performance",
                    "labels": ["enhancement"],
                    "url": "https://github.com/issues/12",
                }
            ],
        }

        title, body = self.auditor.format_issue_content(audit_res)
        self.assertIn("2026-W43", title)
        self.assertIn("py/empty-except", body)
        self.assertIn("Optimize performance", body)
        self.assertIn("CI", body)

    def test_create_or_update_weekly_issue_creates_new(self):
        audit_res = {
            "week_tag": "2026-W43",
            "timestamp": "2026-10-26T20:00:00",
            "summary": {"alerts_count": 1, "failures_count": 0, "open_issues_count": 0},
            "alerts": [],
            "workflow_failures": [],
            "open_issues": [],
        }

        # First subprocess call: gh issue list returns [] (no existing issue)
        # Second subprocess call: gh issue create returns issue URL
        res_list = Mock(returncode=0, stdout="[]")
        res_create = Mock(returncode=0, stdout="https://github.com/test-owner/test-repo/issues/99\n")

        with patch("subprocess.run", side_effect=[res_list, res_create]):
            created, url = self.auditor.create_or_update_weekly_issue(audit_res)
            self.assertTrue(created)
            self.assertEqual(url, "https://github.com/test-owner/test-repo/issues/99")

    def test_create_or_update_weekly_issue_updates_existing(self):
        audit_res = {
            "week_tag": "2026-W43",
            "timestamp": "2026-10-26T20:00:00",
            "summary": {"alerts_count": 1, "failures_count": 0, "open_issues_count": 0},
            "alerts": [],
            "workflow_failures": [],
            "open_issues": [],
        }

        # First subprocess call: gh issue list returns existing issue
        # Second subprocess call: gh issue comment returns 0
        res_list = Mock(returncode=0, stdout=json.dumps([{"number": 99, "url": "https://github.com/test/issues/99"}]))
        res_comment = Mock(returncode=0, stdout="")

        with patch("subprocess.run", side_effect=[res_list, res_comment]):
            created, url = self.auditor.create_or_update_weekly_issue(audit_res)
            self.assertFalse(created)
            self.assertEqual(url, "https://github.com/test/issues/99")

    def test_maintenance_agent_tool_inspect_github_health(self):
        agent = MaintenanceAgent()
        fake_result = {"has_problems": False, "summary": {"alerts_count": 0}}
        with patch("src.diagnostics.github_auditor.GitHubAuditor.run_full_audit", return_value=fake_result):
            res = agent.tool_inspect_github_health()
            self.assertEqual(res, fake_result)


if __name__ == "__main__":
    unittest.main()
