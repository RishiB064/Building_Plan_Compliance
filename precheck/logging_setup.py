"""D5: structured JSON logs with a correlation id carried in a contextvar."""
import contextvars
import json
import logging
import sys
import time

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record):
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
               "level": record.levelname, "service": self.service, "correlation_id": correlation_id.get(),
               "msg": record.getMessage()}
        extra = getattr(record, "extra_fields", None)
        if extra:
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup(service: str) -> logging.Logger:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger(service)


def log(logger: logging.Logger, msg: str, **fields):
    logger.info(msg, extra={"extra_fields": fields})
