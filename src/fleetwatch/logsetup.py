"""Logging for the CLI: every line goes through `redact()`, and the HTTP and MCP libraries stay at WARNING.

With `-v` the root logger is DEBUG. Left alone, the MCP SDK would then log every SSE message raw
(`mcp.client.streamable_http`), stream keys included, and httpx2 every request URL. Those loggers are held at
WARNING whatever the verbosity, and a filter on each root handler redacts the formatted message (and any
traceback) of every record as a second line of defence.
"""

import logging

from fleetwatch.redact import redact

FORMAT = "%(asctime)s %(levelname)s %(message)s"
# The MCP SDK, the httpx2 fork it bundles (and its httpcore2), plus plain httpx and httpcore (doctor, slack_sdk).
QUIET_LOGGERS = ("mcp", "httpx", "httpx2", "httpcore", "httpcore2")


class RedactFilter(logging.Filter):
    """Replaces a record's message with `redact()` of the formatted message, and redacts its traceback text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001  (a bad format string must not hide the record, or leak its args)
            message = str(record.msg)
        record.msg, record.args = str(redact(message)), None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = str(redact(record.exc_text))
        if record.stack_info:
            record.stack_info = str(redact(record.stack_info))
        return True


def configure_logging(verbose: bool) -> None:
    root = logging.getLogger()
    logging.basicConfig(format=FORMAT)  # adds a stderr handler unless one is already there
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for handler in root.handlers:
        if not any(isinstance(f, RedactFilter) for f in handler.filters):
            handler.addFilter(RedactFilter())
