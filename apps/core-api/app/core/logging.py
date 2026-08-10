import json
import logging
import sys
from datetime import UTC, datetime

from opentelemetry import trace

from app.core.config import Settings

_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


class TraceContextFilter(logging.Filter):
    """Injects trace_id/span_id into every log record when a span is
    currently active, so Loki log lines can be correlated with the Tempo
    trace that produced them. Without this, the Tempo→Loki datasource
    link in Grafana would have nothing to actually filter on — no log
    line anywhere would carry a trace_id, making that link
    non-functional; see docs/adr/0010."""

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        context = span.get_span_context()
        if context.is_valid:
            record.trace_id = format(context.trace_id, "032x")
            record.span_id = format(context.span_id, "016x")
        return True


class JsonFormatter(logging.Formatter):
    """Structured JSON logs for production/CI — one object per line, ready
    to ship to Loki/CloudWatch without a separate parsing stage."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOG_RECORD_ATTRS
        }
        if extras:
            payload.update(extras)

        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    # A real terminal (a developer running `uvicorn --reload` by hand)
    # gets human-readable output; anything else — Docker Compose,
    # `pytest`, a container orchestrator — gets JSON. This is keyed off
    # whether stdout is a TTY rather than `environment == "development"`
    # alone, because `docker compose up` runs with ENVIRONMENT=development
    # too (it's not "production"), but its stdout is captured by the
    # Docker daemon for Promtail/Loki to ship, not read by a human
    # directly — it needs the structured form.
    if settings.environment == "development" and sys.stdout.isatty():
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())

    handler.addFilter(TraceContextFilter())
    root.addHandler(handler)

    # uvicorn installs its own handlers on these loggers; defer to the root
    # logger's handler/formatter instead so every log line has one shape.
    for noisy_logger in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(noisy_logger).handlers = []
        logging.getLogger(noisy_logger).propagate = True
