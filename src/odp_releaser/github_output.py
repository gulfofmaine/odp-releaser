"""Helpers for GitHub Actions "workflow command" files.

GitHub Actions exposes two append-only files through the environment:
``GITHUB_OUTPUT`` for step outputs and ``GITHUB_STEP_SUMMARY`` for the
Markdown job summary. This module writes to them safely, falling back to
logging when the variables are unset (i.e. running locally).

Nothing here logs secrets; callers must only pass non-sensitive values.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from odp_releaser.logger import logger

GITHUB_OUTPUT_ENV = "GITHUB_OUTPUT"
GITHUB_STEP_SUMMARY_ENV = "GITHUB_STEP_SUMMARY"


def write_github_output(values: dict[str, str]) -> None:
    """Append ``values`` to the ``GITHUB_OUTPUT`` file as step outputs.

    Each key is written using the heredoc delimiter form so that multiline
    values pass through safely::

        key<<EOF_<unique>
        value
        EOF_<unique>

    A unique delimiter is generated per key so it cannot collide with the
    value's content. When ``GITHUB_OUTPUT`` is unset the values are logged at
    info level instead (local runs) and nothing is raised.
    """
    output_path = os.environ.get(GITHUB_OUTPUT_ENV)
    if not output_path:
        for key, value in values.items():
            logger.info("GitHub output %s=%s", key, value)
        return

    with Path(output_path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            delimiter = f"EOF_{uuid.uuid4().hex}"
            handle.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def write_step_summary(markdown: str) -> None:
    """Append ``markdown`` to the ``GITHUB_STEP_SUMMARY`` file.

    When ``GITHUB_STEP_SUMMARY`` is unset the markdown is logged at info level
    instead (local runs) and nothing is raised.
    """
    summary_path = os.environ.get(GITHUB_STEP_SUMMARY_ENV)
    if not summary_path:
        logger.info("Step summary:\n%s", markdown)
        return

    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{markdown}\n")
