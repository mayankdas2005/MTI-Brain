"""Logging configuration using loguru.

Configures a loguru logger with a custom format that conditionally includes the
function name.  Also provides an :class:`InterceptHandler` to redirect standard
library ``logging`` calls through loguru.
"""

import logging
import sys

from app.core.config import settings
from loguru import logger


def format_with_function(record):
    """Return a loguru format string, conditionally including the function name.

    When the log record originates from module-level code (``<module>``), the
    function name is omitted to keep output clean.

    Args:
        record: A loguru record dict containing log metadata.

    Returns:
        A loguru format string with colour and layout markup.
    """
    func = record["function"]
    if func == "<module>":
        return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level.icon}</level>  <level>{level: <8}</level> | <cyan>{file.name}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>\n{exception}"
    return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level.icon}</level>  <level>{level: <8}</level> | <cyan>{file.name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>\n{exception}"


class InterceptHandler(logging.Handler):
    """Intercept standard library logging and route it through loguru.

    Attach this handler to any :class:`logging.Logger` to forward its messages
    to the loguru sink while preserving the correct call-site information.
    """

    def emit(self, record: logging.LogRecord):
        """Forward a standard logging record to loguru.

        Walks the call stack to determine the correct depth so that loguru
        reports the original call site rather than this handler.

        Args:
            record: The :class:`logging.LogRecord` emitted by the standard
                library logger.
        """
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


logger.remove()
logger.add(
    sys.stderr, format=format_with_function, colorize=True, level=settings.LOG_LEVEL
)

# logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

# for logger_name in [
#     "uvicorn",
#     "uvicorn.access",
#     "uvicorn.error",
#     "fastapi",
#     "sqlalchemy.engine",
#     "watchfiles",
# ]:
#     logging.getLogger(logger_name).handlers = [InterceptHandler()]


# Export the configured logger
__all__ = ["logger"]
