"""``odp-releaser validate`` -- static checks for the configs this project reads.

Every command here runs the same checks :mod:`odp_releaser.validation` uses
elsewhere (schema validation, unknown-key detection, and the semantic checks
in :mod:`odp_releaser.validation.image_manifest` /
:mod:`odp_releaser.validation.deploy_targets`), but never calls the GitHub
API and never writes to a manifest -- these are read-only, offline checks
meant to run in CI on a config repo or as a pre-commit hook, catching a
mistake before it reaches a real release.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from odp_releaser.bump_images import DEFAULT_CONFIG_PATH
from odp_releaser.notify import DEFAULT_TARGETS_PATH
from odp_releaser.validation.deploy_targets import validate_deploy_targets
from odp_releaser.validation.diagnostics import Severity
from odp_releaser.validation.image_manifest import validate_image_manifest

if TYPE_CHECKING:
    from odp_releaser.validation.diagnostics import Diagnostics

app = typer.Typer(
    no_args_is_help=True,
    help="Validate odp-releaser configs statically, without touching GitHub",
)

Strict = Annotated[
    bool,
    typer.Option(
        "--strict",
        help="Also fail (exit 1) on warning-only results, not just errors",
    ),
]
NoCheckFiles = Annotated[
    bool,
    typer.Option(
        "--no-check-files",
        help=(
            "Skip filesystem checks on manifests an image_manifest.yaml "
            "references, for a repo whose manifests live outside this "
            "checkout"
        ),
    ),
]
ImageManifestPaths = Annotated[
    list[Path] | None,
    typer.Argument(
        help=(
            "image_manifest.yaml paths to validate. Defaults to "
            f"{DEFAULT_CONFIG_PATH} when omitted; a pre-commit hook appends "
            "the matched filenames here instead."
        ),
    ),
]
DeployTargetsPaths = Annotated[
    list[Path] | None,
    typer.Argument(
        help=(
            "deploy_targets.yaml paths to validate. Defaults to "
            f"{DEFAULT_TARGETS_PATH} when omitted; a pre-commit hook appends "
            "the matched filenames here instead."
        ),
    ),
]


_SEVERITY_COLORS = {
    Severity.error: typer.colors.RED,
    Severity.warning: typer.colors.YELLOW,
}

_CLEAN_MARK = "✓"


def _encodable(text: str, encoding: str) -> str:
    """``text`` with anything ``encoding`` can't represent replaced.

    Windows consoles default to a legacy code page (cp1252) with no U+2713,
    and click does not shield writes from that -- printing straight through
    raises ``UnicodeEncodeError``, which would turn a *passing* pre-commit
    hook into a traceback. Mangling one character in a diagnostic beats
    losing the whole report.
    """
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return text.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
    except LookupError:
        # An encoding name Python doesn't know; ASCII is the safe floor.
        return text.encode("ascii", errors="replace").decode("ascii")
    return text


def _stream_encoding(stream: object) -> str:
    return getattr(stream, "encoding", None) or "utf-8"


def _echo(text: str, *, err: bool = False, fg: str | None = None) -> None:
    """Write one line, surviving a stream that can't encode every character."""
    stream = sys.stderr if err else sys.stdout
    typer.secho(_encodable(text, _stream_encoding(stream)), err=err, fg=fg)


def _clean_mark() -> str:
    """``✓``, or ``OK`` when stdout can't encode it (see :func:`_echo`).

    Resolved per call rather than cached so it follows whichever stream is
    actually in use, and kept separate from :func:`_encodable`'s replacement
    so a clean run reads as ``OK <path>`` rather than ``? <path>``.
    """
    if _encodable(_CLEAN_MARK, _stream_encoding(sys.stdout)) != _CLEAN_MARK:
        return "OK"
    return _CLEAN_MARK


def _render(diagnostics: Diagnostics, *, strict: bool) -> bool:
    """Print one file's diagnostics and report whether they should fail the run.

    Errors and warnings go to stderr (`err=True`) so a pre-commit hook's own
    output is never confused with data on stdout; a clean file instead prints
    a short one-line summary to stdout, so a passing run isn't silent either.

    Each line is colored by severity so a long report can be skimmed for the
    errors that actually fail the run. `typer.secho` (via click) strips the
    color codes when the stream isn't a terminal, so piped and pre-commit
    output stays plain text.
    """
    for diagnostic in diagnostics.diagnostics:
        _echo(diagnostic.render(), err=True, fg=_SEVERITY_COLORS[diagnostic.severity])
    if not diagnostics.diagnostics:
        _echo(f"{_clean_mark()} {diagnostics.file}", fg=typer.colors.GREEN)
    return diagnostics.failed(strict=strict)


@app.command(name="image-manifest")
def image_manifest_command(
    paths: ImageManifestPaths = None,
    *,
    strict: Strict = False,
    no_check_files: NoCheckFiles = False,
) -> None:
    """Validate one or more `image_manifest.yaml` configs.

    Defaults to checking `.github/image_manifest.yaml` when no paths are
    given. Exits 1 if any file has errors (or, with `--strict`, any
    warnings).
    """
    failed = False
    for path in paths or [DEFAULT_CONFIG_PATH]:
        diagnostics = validate_image_manifest(path, check_files=not no_check_files)
        if _render(diagnostics, strict=strict):
            failed = True
    if failed:
        raise typer.Exit(1)


@app.command(name="deploy-targets")
def deploy_targets_command(
    paths: DeployTargetsPaths = None,
    *,
    strict: Strict = False,
) -> None:
    """Validate one or more `deploy_targets.yaml` configs.

    Defaults to checking `.github/deploy_targets.yaml` when no paths are
    given. Exits 1 if any file has errors (or, with `--strict`, any
    warnings).
    """
    failed = False
    for path in paths or [Path(DEFAULT_TARGETS_PATH)]:
        diagnostics = validate_deploy_targets(path)
        if _render(diagnostics, strict=strict):
            failed = True
    if failed:
        raise typer.Exit(1)
