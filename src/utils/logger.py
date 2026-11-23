import logging
import re
from logging.handlers import RotatingFileHandler
from src.config import LOGS_DIR

class CredentialRedactionFilter(logging.Filter):
    _patterns = (
        (re.compile(r"(https://api\.telegram\.org/bot)\d+:[A-Za-z0-9_-]+"), r"\1[REDACTED]"),
        (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+"), r"\1[REDACTED]"),
        (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]+"), "[REDACTED]"),
        (re.compile(r"(?i)(token|key|password|passwd|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_.-]+['\"]?"), r"\1=[REDACTED]"),
    )

    def filter(self, record):
        message = record.getMessage()
        for pattern, replacement in self._patterns:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True

def setup_global_logger():
    root_logger = logging.getLogger()
    filter_instance = CredentialRedactionFilter()
    for handler in root_logger.handlers:
        handler.addFilter(filter_instance)
    
    # Ensure all root logger output goes through redaction
    if not any(isinstance(f, CredentialRedactionFilter) for f in root_logger.filters):
        root_logger.addFilter(filter_instance)
