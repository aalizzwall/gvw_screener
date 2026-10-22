import os
import re
import sys
import json
import time
import shutil
import logging
import subprocess
from typing import Dict, Any, List, Optional, Tuple, Union

from src.state.ai_provider import AIProviderPreferenceStore
from src.analysis.model_prober import get_intra_provider_pool
from src.analysis.ai_analyst import (
    _PROVIDERS,
    _provider_key,
    _resolve_model,
    _request_completion,
)

logger = logging.getLogger("gvw.agent.solver")

# Guardrails
ALLOWED_PREFIXES = (
    "src/data/",
    "src/indicators/",
    "src/analysis/",
    "src/reporting/",
    "src/providers/",
    "src/core/",
    "tests/",
)

FORBIDDEN_PREFIXES = (
    "gvw_bot/",
    "src/agent/",
    "src/diagnostics/",
    ".env",
    "data/",
    "logs/",
    ".github/",
    "systemd/",
    "requirements.txt",
    "setup.py",
    "LICENSE",
)

MAX_FILES_CHANGED = 3
MAX_DIFF_LINES = 150
MAX_REFLECTIONS = 2


class SecurityGuardrailError(Exception):
    """Raised when an autonomous agent action violates security boundaries."""
    pass


class IssueSolver:
    """
    Autonomous Issue Solver Agent with strict guardrails:
    1. Isolated sandbox execution via git worktree.
    2. Strict file path whitelisting (only src/ business logic and tests/).
    3. Blast radius limitation (max 3 files, max 150 lines).
    4. Test integrity guard (anti-cheating: forbids deleting test assertions).
    5. Safe delivery: Creates a PR for human review; never directly merges or deploys.
    """

    def __init__(
        self,
        repo_root: Optional[str] = None,
        ai_provider_store: Optional[AIProviderPreferenceStore] = None,
    ):
        self.repo_root = repo_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.ai_provider_store = ai_provider_store or AIProviderPreferenceStore()

    def fetch_issue(self, issue_id: Union[int, str]) -> Dict[str, Any]:
        """Fetches issue details from GitHub using gh CLI."""
        clean_id = str(issue_id).lstrip("#").strip()
        cmd = ["gh", "issue", "view", clean_id, "--json", "number,title,body,state,labels"]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=self.repo_root, timeout=20)
        if res.returncode != 0:
            raise RuntimeError(f"無法取得 Issue #{clean_id} 資訊：{res.stderr.strip() or '未知錯誤'}")
        return json.loads(res.stdout)

    def _call_llm(self, prompt: str) -> str:
        """Invokes the configured AI provider to solve the problem with Two-Tier Fallback."""
        chain = self.ai_provider_store.get_chain()
        local_url = self.ai_provider_store.get_local_base_url()

        for entry in chain:
            name, model = entry.split("/", 1) if "/" in entry else (entry, "")
            if name not in _PROVIDERS:
                continue
            key = _provider_key(name, local_base_url=local_url)
            if not key:
                continue

            pool = get_intra_provider_pool(name, key, local_base_url=local_url, preferred_model=model)
            for cand_model in pool:
                try:
                    actual_model = _resolve_model(name, cand_model, local_base_url=local_url)
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "You are an expert autonomous software engineer solving a GitHub issue for the 'gvw_screener' repository.\n"
                                "Safety Guardrails:\n"
                                "1. ONLY modify files in allowed directories: "
                                + ", ".join(ALLOWED_PREFIXES)
                                + "\n"
                                "2. NEVER delete or weaken existing test assertions in tests/.\n"
                                "3. Keep changes minimal (max 3 files, max 150 lines total).\n"
                                "4. Return strictly valid JSON with this format:\n"
                                "{\n"
                                '  "explanation": "brief explanation",\n'
                                '  "files": [\n'
                                '    {"path": "src/path/to/file.py", "content": "FULL NEW CONTENT"}\n'
                                "  ]\n"
                                "}"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ]
                    return _request_completion(name, key, actual_model, messages, max_tokens=3000)
                except Exception as e:
                    logger.warning(f"Solver LLM failed with {name}/{cand_model}: {e}")
                    status = getattr(getattr(e, "response", None), "status_code", None)
                    if status == 401:
                        break
                    continue

        raise RuntimeError("所有 AI Provider 與備援模型均無法調用，無法生成修復代碼。")

    def _parse_llm_json(self, raw_resp: str) -> Dict[str, Any]:
        """Strips markdown fences and parses JSON."""
        cleaned = raw_resp.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            return json.loads(cleaned)
        except Exception as e:
            raise ValueError(f"AI 回傳的內容不是合法 JSON：{e}\n回傳內容：{raw_resp[:300]}")

    def validate_file_paths(self, files: List[Dict[str, str]]) -> None:
        """Enforces path whitelists and blacklists."""
        if len(files) > MAX_FILES_CHANGED:
            raise SecurityGuardrailError(
                f"安全阻斷：AI 試圖修改 {len(files)} 個檔案，超出允許上限 ({MAX_FILES_CHANGED} 個)。"
            )

        for f in files:
            path = f.get("path", "").strip().lstrip("/")
            if ".." in path:
                raise SecurityGuardrailError(f"安全阻斷：路徑包含無效字元 (..)：{path}")

            # Check forbidden
            if any(path.startswith(fb) or path == fb for fb in FORBIDDEN_PREFIXES):
                raise SecurityGuardrailError(
                    f"安全阻斷：禁止修改受保護的系統或基礎設施檔案：{path}"
                )

            # Check allowed
            if not any(path.startswith(al) for al in ALLOWED_PREFIXES):
                raise SecurityGuardrailError(
                    f"安全阻斷：路徑未在允許修復的白名單目錄中：{path}"
                )

    def validate_diff_and_anti_cheat(self, sandbox_path: str) -> None:
        """Enforces line limit and forbids deleting test assertions."""
        # 1. Check diff line count
        diff_stat = subprocess.check_output(
            ["git", "diff", "--numstat"],
            cwd=sandbox_path,
            text=True,
        ).strip()

        total_lines = 0
        file_count = 0
        for line in diff_stat.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                try:
                    added = int(parts[0]) if parts[0] != "-" else 0
                    deleted = int(parts[1]) if parts[1] != "-" else 0
                    total_lines += added + deleted
                    file_count += 1
                except ValueError:
                    pass

        if file_count > MAX_FILES_CHANGED:
            raise SecurityGuardrailError(
                f"安全阻斷：實際變更檔案數 ({file_count}) 超出上限 ({MAX_FILES_CHANGED})。"
            )

        if total_lines > MAX_DIFF_LINES:
            raise SecurityGuardrailError(
                f"安全阻斷：變更代碼行數 ({total_lines}) 超出允許上限 ({MAX_DIFF_LINES} 行)。"
            )

        # 2. Check anti-cheat on tests/
        test_diff = subprocess.check_output(
            ["git", "diff", "tests/"],
            cwd=sandbox_path,
            text=True,
        )
        for line in test_diff.splitlines():
            # Check for deleted assertions
            if line.startswith("-") and not line.startswith("---"):
                stripped = line[1:].strip()
                if "self.assert" in stripped or stripped.startswith("assert "):
                    raise SecurityGuardrailError(
                        f"安全阻斷：偵測到作弊行為！AI 試圖刪除測試斷言：{stripped}"
                    )

    def _find_relevant_code(self, issue_body: str) -> Dict[str, str]:
        """Extracts files mentioned in issue body, or defaults to core modules."""
        relevant = {}
        # Look for paths in issue body
        found_paths = set(re.findall(r"(?:src|tests)/[a-zA-Z0-9_/]+\.py", issue_body))

        for path in found_paths:
            full_path = os.path.join(self.repo_root, path)
            if os.path.exists(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        relevant[path] = f.read()
                except Exception:
                    pass

        # If none found, provide basic hints
        if not relevant:
            candidates = [
                "src/data/twse_client.py",
                "src/indicators/trend_filter.py",
                "src/analysis/screener.py",
            ]
            for c in candidates:
                full_path = os.path.join(self.repo_root, c)
                if os.path.exists(full_path):
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            relevant[c] = f.read()
                    except Exception:
                        pass
        return relevant

    def solve_issue(self, issue_id: Union[int, str]) -> Dict[str, Any]:
        """
        Executes autonomous issue resolution pipeline.
        Returns result dict with PR URL, modified files, explanation, etc.
        """
        clean_id = str(issue_id).lstrip("#").strip()
        issue = self.fetch_issue(clean_id)

        if issue.get("state") == "CLOSED":
            return {
                "success": False,
                "message": f"Issue #{clean_id} 已經是關閉狀態 (CLOSED)，無須修復。",
            }

        title = issue.get("title", "")
        body = issue.get("body", "")
        logger.info(f"Starting autonomous solver for Issue #{clean_id}: {title}")

        sandbox_id = f"{clean_id}_{int(time.time())}"
        sandbox_path = f"/tmp/gvw_solver_{sandbox_id}"
        branch_name = f"fix/issue-{clean_id}"

        # 1. Create isolated sandbox via git worktree
        try:
            # Delete local branch if already exists from earlier abandoned runs
            subprocess.run(["git", "branch", "-D", branch_name], capture_output=True, cwd=self.repo_root)

            wt_res = subprocess.run(
                ["git", "worktree", "add", sandbox_path, "-b", branch_name],
                capture_output=True,
                text=True,
                cwd=self.repo_root,
                timeout=30,
            )
            if wt_res.returncode != 0:
                raise RuntimeError(f"無法建立隔離沙盒 (git worktree)：{wt_res.stderr.strip()}")

            relevant_files = self._find_relevant_code(body)
            files_context = "\n\n".join(
                [f"=== File: {path} ===\n{content}" for path, content in relevant_files.items()]
            )

            current_prompt = (
                f"GitHub Issue #{clean_id}\n"
                f"Title: {title}\n"
                f"Description:\n{body}\n\n"
                f"Relevant Code:\n{files_context}\n\n"
                f"Please implement the fix and output strictly in JSON format."
            )

            # 2. Reflection loop: Generate patch, test, and self-correct if failed
            explanation = ""
            modified_files = []
            test_passed = False

            for attempt in range(MAX_REFLECTIONS + 1):
                logger.info(f"Solver iteration {attempt + 1}/{MAX_REFLECTIONS + 1}...")
                raw_llm = self._call_llm(current_prompt)
                parsed = self._parse_llm_json(raw_llm)

                explanation = parsed.get("explanation", "")
                files = parsed.get("files", [])
                if not files:
                    raise ValueError("AI 沒有回傳任何檔案變更。")

                # Validate Guardrails
                self.validate_file_paths(files)

                # Apply changes to sandbox
                modified_files = []
                for f in files:
                    fpath = f["path"].strip().lstrip("/")
                    full_target = os.path.join(sandbox_path, fpath)
                    os.makedirs(os.path.dirname(full_target), exist_ok=True)
                    with open(full_target, "w", encoding="utf-8") as out:
                        out.write(f["content"])
                    modified_files.append(fpath)

                # Validate Diff & Anti-cheat
                self.validate_diff_and_anti_cheat(sandbox_path)

                # Run Tests in sandbox
                test_cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
                test_res = subprocess.run(
                    test_cmd,
                    capture_output=True,
                    text=True,
                    cwd=sandbox_path,
                    timeout=90,
                )

                if test_res.returncode == 0:
                    test_passed = True
                    logger.info("All unit tests passed successfully!")
                    break
                else:
                    logger.warning(f"Tests failed on iteration {attempt + 1}:\n{test_res.stderr}")
                    if attempt < MAX_REFLECTIONS:
                        current_prompt = (
                            f"Your previous fix resulted in test failures:\n\n"
                            f"=== Test Output ===\n{test_res.stderr}\n\n"
                            f"Please analyze the failure, correct the solution, and return valid JSON."
                        )
                    else:
                        raise RuntimeError(
                            f"修復後的單元測試未通過 (已重試 {MAX_REFLECTIONS} 次)：\n{test_res.stderr[-400:]}"
                        )

            if not test_passed:
                raise RuntimeError("單元測試未通過。")

            # 3. Commit changes in sandbox
            commit_msg = f"fix(agent): resolve issue #{clean_id} - {title}"
            subprocess.run(["git", "add", "."], cwd=sandbox_path, check=True)
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=sandbox_path, check=True)

            # 4. Push branch
            push_res = subprocess.run(
                ["git", "push", "-u", "origin", branch_name, "--force"],
                capture_output=True,
                text=True,
                cwd=sandbox_path,
                timeout=45,
            )
            if push_res.returncode != 0:
                raise RuntimeError(f"Git push 失敗：{push_res.stderr.strip()}")

            # 5. Create Pull Request
            pr_body = (
                f"## 🤖 Autonomous Fix by GVW Doctor Agent\n\n"
                f"**Issue**: Closes #{clean_id} ({title})\n"
                f"**Summary**: {explanation}\n"
                f"**Modified Files**: `{', '.join(modified_files)}`\n\n"
                f"### 🛡️ Guardrails Validation\n"
                f"- [x] Isolated sandbox execution\n"
                f"- [x] Path whitelisting verified\n"
                f"- [x] Blast radius verified (≤3 files, ≤150 lines)\n"
                f"- [x] Test integrity verified (no assertions removed)\n"
                f"- [x] Unit tests passed 100% ✅\n"
            )
            pr_res = subprocess.run(
                ["gh", "pr", "create", "--title", commit_msg, "--body", pr_body, "--head", branch_name, "--base", "main"],
                capture_output=True,
                text=True,
                cwd=sandbox_path,
                timeout=30,
            )
            if pr_res.returncode != 0:
                raise RuntimeError(f"建立 PR 失敗：{pr_res.stderr.strip()}")

            pr_url = pr_res.stdout.strip()
            return {
                "success": True,
                "issue_id": clean_id,
                "title": title,
                "pr_url": pr_url,
                "branch": branch_name,
                "modified_files": modified_files,
                "explanation": explanation,
            }

        finally:
            # Always clean up isolated worktree sandbox
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", sandbox_path],
                    capture_output=True,
                    cwd=self.repo_root,
                )
            except Exception:
                pass
            if os.path.exists(sandbox_path):
                shutil.rmtree(sandbox_path, ignore_errors=True)

