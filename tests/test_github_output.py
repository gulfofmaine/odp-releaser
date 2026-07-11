from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from odp_releaser.github_output import write_github_output, write_step_summary

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# --- write_github_output -----------------------------------------------------


def test_write_github_output_heredoc_structure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    write_github_output({"key": "value"})

    lines = output.read_text().splitlines()
    assert lines[0].startswith("key<<")
    delimiter = lines[0].split("<<", 1)[1]
    assert delimiter  # a non-empty unique delimiter was generated
    assert lines[1] == "value"
    assert lines[2] == delimiter


def test_write_github_output_multiline_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    write_github_output({"table": "row1\nrow2\nrow3"})

    lines = output.read_text().splitlines()
    assert lines[0].startswith("table<<")
    delimiter = lines[0].split("<<", 1)[1]
    assert lines[1:4] == ["row1", "row2", "row3"]
    assert lines[4] == delimiter


def test_write_github_output_unset_logs_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    with caplog.at_level(logging.INFO, logger="odp-releaser"):
        write_github_output({"key": "value"})

    assert any("key" in record.getMessage() for record in caplog.records)
    assert any("value" in record.getMessage() for record in caplog.records)


def test_write_github_output_appends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    write_github_output({"first": "1"})
    write_github_output({"second": "2"})

    contents = output.read_text()
    assert "first<<" in contents
    assert "second<<" in contents


# --- write_step_summary ------------------------------------------------------


def test_write_step_summary_writes_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    write_step_summary("# Heading\n\nSome text")

    assert "# Heading\n\nSome text" in summary.read_text()


def test_write_step_summary_unset_logs_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    with caplog.at_level(logging.INFO, logger="odp-releaser"):
        write_step_summary("# Heading")

    assert any("# Heading" in record.getMessage() for record in caplog.records)


def test_write_step_summary_appends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    write_step_summary("first summary")
    write_step_summary("second summary")

    contents = summary.read_text()
    assert "first summary" in contents
    assert "second summary" in contents
