import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_HANDLER_NAME = "geomeasure-json"

# Attributes every LogRecord has; anything else on a record was passed via `extra=`.
# Uvicorn also attaches an ANSI-coloured copy of its message as `color_message`.
_IGNORED_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object per line (suitable for log aggregation)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if request_id := request_id_var.get():
            payload["request_id"] = request_id
        payload.update(
            {key: value for key, value in vars(record).items() if key not in _IGNORED_ATTRS}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(JsonFormatter())

    # Each app instance calls this, so replace only our own handler and keep others (pytest's).
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if h.get_name() != _HANDLER_NAME] + [handler]
    root.setLevel(level)
    # pyogrio reports GDAL warnings (e.g. "Non closed ring detected") as Python warnings.
    logging.captureWarnings(True)

    # Send Uvicorn's logs through the same JSON handler. Requests are logged by our middleware
    # (with request IDs and timings), so Uvicorn's own access log is turned off.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
