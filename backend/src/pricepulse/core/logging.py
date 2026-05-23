import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    level_int = getattr(logging, level, logging.INFO)
    # uvicorn configures its own loggers and removes the default root
    # handler, so `basicConfig` becomes a no-op once it runs. Forcibly
    # attach a stderr StreamHandler to the root logger so our structlog
    # output actually surfaces in production.
    root = logging.getLogger()
    root.setLevel(level_int)
    has_stream = any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
        for h in root.handlers
    )
    if not has_stream:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
