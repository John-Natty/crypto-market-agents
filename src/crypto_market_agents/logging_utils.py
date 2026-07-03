"""Logging helpers with centralized secret redaction."""

from __future__ import annotations

import logging
import os

from crypto_market_agents.security import redact_text


DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts secrets from rendered log messages."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(level: str | None = None) -> None:
    """Configure root logging with a simple redacting formatter."""

    selected_level = _log_level(level or os.getenv("LOG_LEVEL") or "INFO")
    root_logger = logging.getLogger()

    if not root_logger.handlers:
        root_logger.addHandler(logging.StreamHandler())

    for handler in root_logger.handlers:
        handler.setFormatter(RedactingFormatter(DEFAULT_LOG_FORMAT))

    root_logger.setLevel(selected_level)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""

    return logging.getLogger(name)


def _log_level(value: str) -> int:
    normalized = str(value).strip().upper()
    level = getattr(logging, normalized, None)
    if isinstance(level, int):
        return level

    return logging.INFO
