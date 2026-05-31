"""Structured logging via structlog.

Owner: platform segment.
"""

from __future__ import annotations

import logging

import structlog

# Loggers cần suppress ở WARNING+ để không nhấn chìm business logs
_NOISY_LOGGERS = [
    "sqlalchemy.engine",        # SQL echo — controlled separately via DB_ECHO
    "sqlalchemy.engine.Engine",
    "sqlalchemy.pool",
    "httpx",                    # HTTP request/response — quá verbose ở INFO
]


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))

    # Suppress noisy third-party loggers — chỉ WARNING+ mới hiện
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
