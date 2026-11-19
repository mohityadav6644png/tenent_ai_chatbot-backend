"""
Core Logging (app/core/logging.py)
Structured JSON logging with sensitive data scrubbing (emails, questions, SQL, rows).
"""

import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
SQL_REGEX = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b.*?\b(FROM|INTO|TABLE|SET)\b",
    re.IGNORECASE,
)
MONGO_REGEX = re.compile(r"(\$match|\$group|\$project|\$lookup|\$unwind|\$sort)", re.IGNORECASE)


class ScrubFilter(logging.Filter):
    """Scrub sensitive information such as emails, questions, SQL and Mongo pipelines."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        msg = EMAIL_REGEX.sub("[REDACTED_EMAIL]", msg)
        msg = SQL_REGEX.sub("[REDACTED_SQL]", msg)
        msg = MONGO_REGEX.sub("[REDACTED_PIPELINE]", msg)
        record.msg = msg
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Format log records as clean JSON objects with request_id context."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or request_id_ctx.get(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
handler.addFilter(ScrubFilter())

logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(handler)
logger.propagate = False
