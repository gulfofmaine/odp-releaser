from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
import typer.testing

from odp_releaser.main import app
from odp_releaser.validation.cli import (
    _SEVERITY_COLORS,
    _clean_mark,
    _echo,
    _encodable,
)
from odp_releaser.validation.diagnostics import Severity

MANIFESTS_DIR = Path(__file__).parent / "manifests"
CLEAN_MANIFEST = MANIFESTS_DIR / "dagster_helm_kustomize" / "image_manifest.yaml"
KEY_ERROR_MANIFEST = MANIFESTS_DIR / "key_error" / "image_manifest.yaml"
MISSING_FILE_MANIFEST = MANIFESTS_DIR / "push" / "image_manifest.yaml"

CLEAN_TARGETS_CONTENT = "- owner: gulfofmaine\n  repo: some-deploy-repo\n"


def _invoke(*args: str, input_: str | None = None) -> typer.testing.Result:
    runner = typer.testing.CliRunner()
    return runner.invoke(app, ["validate", *args], input=input_)


# --- help ------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["image-manifest", "--help"],
        ["deploy-targets", "--help"],
    ],
)
def test_help_works(args: list[str]) -> None:
    result = _invoke(*args)
    assert result.exit_code == 0, result.output


# --- image-manifest ---------------------------------------------------------


def test_image_manifest_clean_file_exits_zero() -> None:
    result = _invoke("image-manifest", str(CLEAN_MANIFEST))
    assert result.exit_code == 0, result.output
    assert "✓" in result.stdout
    assert str(CLEAN_MANIFEST) in result.stdout


def test_image_manifest_broken_file_exits_one_with_message_on_stderr() -> None:
    result = _invoke("image-manifest", str(KEY_ERROR_MANIFEST))
    assert result.exit_code == 1
    assert "placeholder" in result.stderr
    assert result.stdout == ""


def test_image_manifest_multiple_paths_mixed_reports_both() -> None:
    result = _invoke("image-manifest", str(CLEAN_MANIFEST), str(KEY_ERROR_MANIFEST))
    assert result.exit_code == 1
    assert str(CLEAN_MANIFEST) in result.stdout
    assert "placeholder" in result.stderr
    assert str(KEY_ERROR_MANIFEST) in result.stderr


def test_image_manifest_no_check_files_flips_missing_manifest_to_clean() -> None:
    without_flag = _invoke("image-manifest", str(MISSING_FILE_MANIFEST))
    assert without_flag.exit_code == 1

    with_flag = _invoke(
        "image-manifest", "--no-check-files", str(MISSING_FILE_MANIFEST)
    )
    assert with_flag.exit_code == 0, with_flag.output
    assert "✓" in with_flag.stdout


# --- deploy-targets -----------------------------------------------------------


def test_deploy_targets_clean_file_exits_zero(tmp_path: Path) -> None:
    targets = tmp_path / "deploy_targets.yaml"
    targets.write_text(CLEAN_TARGETS_CONTENT)

    result = _invoke("deploy-targets", str(targets))
    assert result.exit_code == 0, result.output
    assert "✓" in result.stdout


def test_deploy_targets_broken_file_exits_one_with_message_on_stderr(
    tmp_path: Path,
) -> None:
    targets = tmp_path / "deploy_targets.yaml"
    targets.write_text("- owner: gulfofmaine\n  repo: gulfofmaine/some-repo\n")

    result = _invoke("deploy-targets", str(targets))
    assert result.exit_code == 1
    assert "'/'" in result.stderr
    assert result.stdout == ""


def test_deploy_targets_warning_only_exits_zero_without_strict_one_with(
    tmp_path: Path,
) -> None:
    targets = tmp_path / "deploy_targets.yaml"
    # The same target twice: a warning, since the identical dispatch is sent
    # twice, but not an error — it still works.
    targets.write_text(
        "- owner: gulfofmaine\n  repo: some-deploy-repo\n"
        "- owner: gulfofmaine\n  repo: some-deploy-repo\n"
    )

    result = _invoke("deploy-targets", str(targets))
    assert result.exit_code == 0, result.output
    assert "duplicates" in result.stderr

    strict_result = _invoke("deploy-targets", "--strict", str(targets))
    assert strict_result.exit_code == 1


# --- color ---------------------------------------------------------------------


def test_every_severity_has_a_color() -> None:
    assert set(_SEVERITY_COLORS) == set(Severity)


def test_diagnostics_are_colored_by_severity(tmp_path: Path) -> None:
    targets = tmp_path / "deploy_targets.yaml"
    # One warning (duplicate target) and one error (owner/repo in repo).
    targets.write_text(
        "- owner: gulfofmaine\n  repo: some-deploy-repo\n"
        "- owner: gulfofmaine\n  repo: some-deploy-repo\n"
        "- owner: gulfofmaine\n  repo: gulfofmaine/some-repo\n"
    )

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app, ["validate", "deploy-targets", str(targets)], color=True
    )

    assert result.exit_code == 1
    assert f"\x1b[{31}m" in result.stderr  # red error
    assert f"\x1b[{33}m" in result.stderr  # yellow warning


# --- output encoding ------------------------------------------------------------


def _stream(encoding: str) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding)


def test_clean_mark_is_a_check_when_stdout_can_encode_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", _stream("utf-8"))
    assert _clean_mark() == "✓"


def test_clean_mark_degrades_to_ascii_on_a_legacy_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Windows consoles default to cp1252, which has no U+2713; printing it
    # would otherwise raise UnicodeEncodeError and fail a passing hook.
    monkeypatch.setattr(sys, "stdout", _stream("cp1252"))
    assert _clean_mark() == "OK"


@pytest.mark.parametrize(
    ("text", "encoding", "expected"),
    [
        ("plain ✓", "utf-8", "plain ✓"),
        ("plain ✓", "cp1252", "plain ?"),
        # cp1252 has £ but not U+2713, so only the latter is mangled.
        ("£5 ✓", "cp1252", "£5 ?"),
        ("£5 ✓", "not-an-encoding", "?5 ?"),
    ],
)
def test_encodable_replaces_only_what_the_stream_cannot_represent(
    text: str, encoding: str, expected: str
) -> None:
    assert _encodable(text, encoding) == expected


def test_echo_does_not_raise_on_a_legacy_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", _stream("cp1252"))
    _echo("clean ✓")
