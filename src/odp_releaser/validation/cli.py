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
        typer.secho(
            diagnostic.render(), err=True, fg=_SEVERITY_COLORS[diagnostic.severity]
        )
    if not diagnostics.diagnostics:
        typer.secho(f"✓ {diagnostics.file}", fg=typer.colors.GREEN)
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
