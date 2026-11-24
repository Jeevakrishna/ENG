"""Logging configuration for the ACBS Knowledge Engine.

Logs are written to ``logs/engine.log`` and echoed to the console. Only the
Python standard library logging module is used.
"""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER_NAME = "acbs"

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(logs_directory: Path, verbose: bool = False) -> logging.Logger:
    """Configure and return the root engine logger.

    Repeated calls replace the existing handlers so that the CLI can control
    verbosity on every invocation.
    """

    logs_directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = logging.FileHandler(logs_directory / "engine.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the engine logger."""

    return logging.getLogger(f"{LOGGER_NAME}.{name}")
