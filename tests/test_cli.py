from __future__ import annotations

import importlib.metadata

import typer.testing

from odp_releaser.main import app


def test_version() -> None:
    """Test that the --version flag works and displays the installed version."""
    runner = typer.testing.CliRunner()
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    expected_version = importlib.metadata.version("odp-releaser")
    assert expected_version in result.stdout
