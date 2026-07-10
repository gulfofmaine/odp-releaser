"""Adapter to make a stdlib ``logging.Logger`` usable as a yamlpath logger.

yamlpath (see ``yamlpath.wrappers.ConsolePrinter``) expects a logger object
with ``info``, ``verbose``, ``warning``, ``error``, ``critical``, and
``debug`` methods. Its ``debug`` accepts extra keyword arguments
(``prefix``, ``data``, ``header``, ``footer``, ``data_header``,
``data_footer``) that stdlib ``logging.Logger.debug`` does not understand,
which raises a ``TypeError`` when yamlpath is used with debug logging
enabled. This adapter translates that interface onto a plain
``logging.Logger`` instance.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging


class YamlPathLoggerAdapter:
    """Wrap a stdlib ``logging.Logger`` to satisfy yamlpath's logger interface."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def info(self, message: str) -> None:
        self._logger.info(message)

    def verbose(self, message: str) -> None:
        # ConsolePrinter treats verbose as a step below debug; stdlib logging
        # has no matching level between INFO and DEBUG, so we map it to INFO.
        self._logger.info(message)

    def warning(self, message: str) -> None:
        self._logger.warning(message)

    def debug(self, message: str, **kwargs: Any) -> None:
        prefix = kwargs.pop("prefix", "")
        header = kwargs.pop("header", "")
        footer = kwargs.pop("footer", "")
        data_header = kwargs.pop("data_header", "")
        data_footer = kwargs.pop("data_footer", "")

        lines: list[str] = []
        if header:
            lines.append(f"{prefix}{header}")
        lines.append(f"{prefix}{message}")

        if "data" in kwargs:
            data = kwargs.pop("data")
            if data_header:
                lines.append(f"{prefix}{data_header}")
            lines.append(f"{prefix}{data!r}")
            if data_footer:
                lines.append(f"{prefix}{data_footer}")

        if footer:
            lines.append(f"{prefix}{footer}")

        self._logger.debug("\n".join(lines))

    def error(self, message: str, exit_code: int | None = None) -> None:
        self._logger.error(message)
        if exit_code is not None:
            sys.exit(exit_code)

    def critical(self, message: str, exit_code: int = 1) -> None:
        self._logger.critical(message)
        sys.exit(exit_code)
