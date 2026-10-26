"""Pi-style core primitives (bash / read_file / write_file / edit_file) for the maintenance agent.

Guardrails (owner-only access is enforced by the Telegram handler):
- every command has a timeout and runs with stdin closed (no interactive prompts)
- output is truncated and secrets are redacted before reaching the LLM / Telegram
- obviously destructive commands are refused
- write/edit refuse credential files and .git internals
"""
import os
import re
import subprocess
from typing import Any, Dict

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 180
MAX_OUTPUT_CHARS = 6000
MAX_READ_LINES = 400

DANGEROUS_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME|/\*)(\s|$)",
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+[^|;]*\bof=/dev/",
    r">\s*/dev/(sd|mmcblk|nvme)",
    r"\b(shutdown|poweroff|halt)\b",
    r"\breboot\b",
    r":\(\)\s*\{.*\};\s*:",
    r"\bchmod\s+-R\s+[0-7]*\s+/(\s|$)",
    r"\bchown\s+-R\s+\S+\s+/(\s|$)",
    r"\bcurl\b[^|;]*\|\s*(sudo\s+)?(ba)?sh\b",
    r"\bwget\b[^|;]*\|\s*(sudo\s+)?(ba)?sh\b",
]

PROTECTED_WRITE_NAMES = {".env", "id_rsa", "id_ed25519", "authorized_keys", "known_hosts"}
PROTECTED_WRITE_PARTS = ("/.git/", "/.ssh/")

_SECRET_KEY = r"[A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|KEY)[A-Za-z0-9_]*"
_ASSIGNMENT = re.compile(rf"(?im)^(\s*(?:export\s+)?{_SECRET_KEY}\s*[=:]\s*)(.+)$")
_TELEGRAM_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}")
_API_KEY_LIKE = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b")


def redact_secrets(text: str) -> str:
    text = _ASSIGNMENT.sub(r"\1***REDACTED***", text)
    text = _TELEGRAM_TOKEN.sub("***REDACTED***", text)
    text = _BEARER.sub(r"\1***REDACTED***", text)
    return _API_KEY_LIKE.sub("***REDACTED***", text)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2:]
    return f"{head}\n... [已截斷 {len(text) - limit} 字元] ...\n{tail}"


def is_dangerous_command(command: str) -> bool:
    return any(re.search(p, command) for p in DANGEROUS_PATTERNS)


def _is_protected_path(path: str) -> bool:
    real = os.path.realpath(os.path.expanduser(path))
    if os.path.basename(real) in PROTECTED_WRITE_NAMES:
        return True
    return any(part in real + "/" for part in PROTECTED_WRITE_PARTS)


class ShellToolkit:
    def __init__(self, cwd: str):
        self.cwd = cwd

    def bash(self, command: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        command = str(command or "").strip()
        if not command:
            return {"status": "error", "error": "command 不可為空"}
        if is_dangerous_command(command):
            return {"status": "refused", "error": "此指令被判定為高風險破壞性操作，已拒絕執行。請改用更精確、範圍更小的指令。"}
        try:
            timeout = max(1, min(int(timeout), MAX_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        try:
            res = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            return {
                "status": "timeout",
                "error": f"指令執行超過 {timeout} 秒已中止（避免互動式或長時間阻塞）",
                "stdout": _truncate(redact_secrets(partial)),
            }
        return {
            "status": "ok" if res.returncode == 0 else "failed",
            "exit_code": res.returncode,
            "stdout": _truncate(redact_secrets(res.stdout)),
            "stderr": _truncate(redact_secrets(res.stderr), 2500),
        }

    def read_file(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> Dict[str, Any]:
        full = os.path.expanduser(str(path))
        if not os.path.isabs(full):
            full = os.path.join(self.cwd, full)
        if os.path.isdir(full):
            return {"status": "error", "error": f"{path} 是目錄，請改用 bash 的 ls。"}
        try:
            offset = max(1, int(offset))
            limit = max(1, min(int(limit), MAX_READ_LINES))
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as exc:
            return {"status": "error", "error": str(exc)}
        chunk = [redact_secrets(line) for line in lines[offset - 1: offset - 1 + limit]]
        numbered = "".join(f"{i}\t{line}" for i, line in enumerate(chunk, start=offset))
        return {
            "status": "ok",
            "path": full,
            "total_lines": len(lines),
            "content": _truncate(numbered),
        }

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        full = os.path.expanduser(str(path))
        if not os.path.isabs(full):
            full = os.path.join(self.cwd, full)
        if _is_protected_path(full):
            return {"status": "refused", "error": "不允許寫入憑證檔或 .git/.ssh 內部檔案。"}
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(str(content))
        except OSError as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "path": full, "bytes": len(str(content).encode("utf-8"))}

    def edit_file(self, path: str, old_text: str, new_text: str) -> Dict[str, Any]:
        full = os.path.expanduser(str(path))
        if not os.path.isabs(full):
            full = os.path.join(self.cwd, full)
        if _is_protected_path(full):
            return {"status": "refused", "error": "不允許編輯憑證檔或 .git/.ssh 內部檔案。"}
        old_text, new_text = str(old_text), str(new_text)
        if not old_text:
            return {"status": "error", "error": "old_text 不可為空"}
        try:
            with open(full, "r", encoding="utf-8") as f:
                original = f.read()
        except OSError as exc:
            return {"status": "error", "error": str(exc)}
        count = original.count(old_text)
        if count == 0:
            return {"status": "error", "error": "找不到 old_text，請先用 read_file 確認原文（含空白與縮排）。"}
        if count > 1:
            return {"status": "error", "error": f"old_text 出現 {count} 次，請提供更多上下文使其唯一。"}
        try:
            with open(full, "w", encoding="utf-8") as f:
                f.write(original.replace(old_text, new_text, 1))
        except OSError as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "path": full}
