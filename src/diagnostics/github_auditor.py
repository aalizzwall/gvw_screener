import json
import logging
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("gvw.diagnostics.github")


class GitHubAuditor:
    """
    Automated inspector for GitHub repository issues, workflow health,
    and CodeQL security scanning alerts.
    """

    def __init__(self, repo: Optional[str] = None):
        self.repo = repo or self._detect_repo()

    def _detect_repo(self) -> str:
        """Attempts to detect owner/repo from git remote URL or defaults to repository target."""
        try:
            res = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                url = res.stdout.strip()
                part = ""
                if url.startswith("git@github.com:"):
                    part = url[len("git@github.com:"):]
                elif url.startswith("https://github.com/"):
                    part = url[len("https://github.com/"):]
                elif url.startswith("http://github.com/"):
                    part = url[len("http://github.com/"):]

                if part.endswith(".git"):
                    part = part[:-4]
                if "/" in part:
                    return part.strip()
        except Exception:
            # Ignore git remote detection error and fall back to default
            pass
        return "aalizzwall/gvw_screener"

    def check_code_scanning_alerts(self) -> List[Dict[str, Any]]:
        """Queries open CodeQL code scanning alerts from GitHub API."""
        try:
            res = subprocess.run(
                ["gh", "api", f"repos/{self.repo}/code-scanning/alerts?state=open"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res.returncode == 0:
                raw = json.loads(res.stdout.strip() or "[]")
                if isinstance(raw, list):
                    return [
                        {
                            "number": a.get("number"),
                            "rule_id": a.get("rule", {}).get("id", "unknown"),
                            "severity": (
                                a.get("rule", {}).get("security_severity_level")
                                or a.get("rule", {}).get("severity", "unknown")
                            ),
                            "description": a.get("rule", {}).get("description", ""),
                            "path": a.get("most_recent_instance", {}).get("location", {}).get("path", ""),
                            "line": a.get("most_recent_instance", {}).get("location", {}).get("start_line"),
                            "url": a.get("html_url", ""),
                        }
                        for a in raw
                    ]
        except Exception as e:
            logger.warning(f"Failed to check code scanning alerts: {e}")
        return []

    def check_workflow_failures(self, days: int = 7) -> List[Dict[str, Any]]:
        """Queries GitHub Actions workflow runs failed within the last N days."""
        try:
            res = subprocess.run(
                [
                    "gh", "run", "list",
                    "--repo", self.repo,
                    "--limit", "30",
                    "--json", "name,conclusion,status,url,headBranch,createdAt,event",
                ],
                capture_output=True,
                text=True,
                timeout=25,
            )
            if res.returncode == 0:
                runs = json.loads(res.stdout.strip() or "[]")
                failures = []
                cutoff = datetime.now() - timedelta(days=days)
                for r in runs:
                    if r.get("conclusion") == "failure":
                        created_at_str = r.get("createdAt")
                        if created_at_str:
                            try:
                                dt = datetime.fromisoformat(
                                    created_at_str.replace("Z", "+00:00")
                                ).replace(tzinfo=None)
                                if dt < cutoff:
                                    continue
                            except Exception:
                                # Ignore timestamp parsing failure
                                pass
                        failures.append({
                            "name": r.get("name"),
                            "branch": r.get("headBranch"),
                            "event": r.get("event"),
                            "created_at": r.get("createdAt"),
                            "url": r.get("url"),
                        })
                return failures
        except Exception as e:
            logger.warning(f"Failed to check workflow failures: {e}")
        return []

    def check_open_issues(self) -> List[Dict[str, Any]]:
        """Queries all currently open issues in repository."""
        try:
            res = subprocess.run(
                [
                    "gh", "issue", "list",
                    "--repo", self.repo,
                    "--state", "open",
                    "--json", "number,title,labels,createdAt,updatedAt,url",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res.returncode == 0:
                issues = json.loads(res.stdout.strip() or "[]")
                if isinstance(issues, list):
                    return [
                        {
                            "number": i.get("number"),
                            "title": i.get("title"),
                            "labels": [
                                l.get("name") for l in i.get("labels", []) if isinstance(l, dict)
                            ],
                            "created_at": i.get("createdAt"),
                            "updated_at": i.get("updatedAt"),
                            "url": i.get("url"),
                        }
                        for i in issues
                    ]
        except Exception as e:
            logger.warning(f"Failed to check open issues: {e}")
        return []

    def run_full_audit(self) -> Dict[str, Any]:
        """
        Executes comprehensive audit covering code scanning alerts,
        CI workflow failures, and issue backlog tracking.
        """
        alerts = self.check_code_scanning_alerts()
        failures = self.check_workflow_failures(days=7)
        open_issues = self.check_open_issues()

        # Filter out weekly audit issues from backlog list
        regular_issues = [
            i for i in open_issues
            if not i.get("title", "").startswith("[Weekly Audit]")
        ]

        # Problems qualify if there are CodeQL alerts or recent CI workflow failures
        has_problems = len(alerts) > 0 or len(failures) > 0

        now = datetime.now()
        cal = now.isocalendar()
        week_tag = f"{cal[0]}-W{cal[1]:02d}"

        return {
            "timestamp": now.isoformat(),
            "week_tag": week_tag,
            "has_problems": has_problems,
            "alerts": alerts,
            "workflow_failures": failures,
            "open_issues": regular_issues,
            "summary": {
                "alerts_count": len(alerts),
                "failures_count": len(failures),
                "open_issues_count": len(regular_issues),
            },
        }

    def format_issue_content(self, audit_result: Dict[str, Any]) -> Tuple[str, str]:
        """Formats title and markdown body for the GitHub Issue."""
        week_tag = audit_result.get("week_tag", "")
        summary = audit_result.get("summary", {})
        alerts = audit_result.get("alerts", [])
        failures = audit_result.get("workflow_failures", [])
        issues = audit_result.get("open_issues", [])

        alerts_count = summary.get("alerts_count", len(alerts))
        failures_count = summary.get("failures_count", len(failures))

        title = f"[Weekly Audit] 發現 {alerts_count} 個弱點與 {failures_count} 個 CI 異常 ({week_tag})"

        body_lines = [
            f"## 📋 GVW 系統與 GitHub 健康巡檢週報 (`{week_tag}`)",
            "",
            "> 這是由 `gvw_bot` 背景排程器每週日自動生成的 GitHub 巡檢報告。",
            "",
            "### 📊 問題摘要",
            f"- 🛡️ **CodeQL 弱點掃描警報**：`{alerts_count}` 項待處理",
            f"- ❌ **過去 7 天 CI/CD 失敗次數**：`{failures_count}` 次",
            f"- 📌 **待追蹤 Open Issues**：`{len(issues)}` 項",
            "",
        ]

        if alerts:
            body_lines.extend([
                "### 🛡️ CodeQL 安全與程式碼品質警報",
                "| # | 規則 (Rule ID) | 嚴重等級 | 檔案位置 | 連結 |",
                "|---|---|---|---|---|",
            ])
            for a in alerts:
                loc = f"`{a.get('path')}:{a.get('line')}`" if a.get("path") else "-"
                body_lines.append(
                    f"| #{a.get('number')} | `{a.get('rule_id')}` | **{a.get('severity')}** | {loc} | [查看警報]({a.get('url')}) |"
                )
            body_lines.append("")

        if failures:
            body_lines.extend([
                "### ❌ GitHub Actions 失敗工作流紀錄 (近 7 天)",
                "| 工作流名稱 | 分支 | 觸發事件 | 執行連結 |",
                "|---|---|---|---|",
            ])
            for f in failures:
                body_lines.append(
                    f"| {f.get('name')} | `{f.get('branch')}` | `{f.get('event')}` | [查看日誌]({f.get('url')}) |"
                )
            body_lines.append("")

        if issues:
            body_lines.extend([
                "### 📌 既有未結案 Issue 追蹤",
                "| # | 標題 | 標籤 | 連結 |",
                "|---|---|---|---|",
            ])
            for i in issues:
                lbls = ", ".join(i.get("labels", [])) or "None"
                body_lines.append(
                    f"| #{i.get('number')} | {i.get('title')} | `{lbls}` | [前往]({i.get('url')}) |"
                )
            body_lines.append("")

        body_lines.extend([
            "---",
            "**建議行動**：請使用 `@aalizzwall` 或維護助手針對以上項目進行分析與修復。",
            f"*巡檢時間戳記: `{audit_result.get('timestamp')}`*",
        ])

        return title, "\n".join(body_lines)

    def create_or_update_weekly_issue(self, audit_result: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Deduplicates by week_tag and creates an issue if not already open for this week.
        Returns (created_new: bool, issue_url: str).
        """
        week_tag = audit_result.get("week_tag", "")
        title, body = self.format_issue_content(audit_result)

        try:
            # 1. Search existing open issue with the same week tag
            search_res = subprocess.run(
                [
                    "gh", "issue", "list",
                    "--repo", self.repo,
                    "--state", "open",
                    "--search", f"[Weekly Audit] in:title {week_tag}",
                    "--json", "number,url",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if search_res.returncode == 0:
                existing = json.loads(search_res.stdout.strip() or "[]")
                if existing and isinstance(existing, list) and len(existing) > 0:
                    url = existing[0].get("url", "")
                    issue_num = str(existing[0].get("number"))
                    # Update comment
                    subprocess.run(
                        [
                            "gh", "issue", "comment", issue_num,
                            "--repo", self.repo,
                            "--body", f"🔄 **巡檢更新**: 再次檢查結果如下：\n\n{body}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    return False, url

            # 2. Create new issue
            create_res = subprocess.run(
                [
                    "gh", "issue", "create",
                    "--repo", self.repo,
                    "--title", title,
                    "--body", body,
                    "--label", "bug,audit",
                ],
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
            logger.error(f"Error creating GitHub audit issue: {e}")
            raise

