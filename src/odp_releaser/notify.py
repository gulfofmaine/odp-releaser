"""Send ``repository_dispatch`` events to every configured deploy target.

Runs as a step in a source repo's GitHub Actions workflow after an image has
been published. It builds the same :class:`ClientPayload` that ``make-payload``
produces, reads the deploy targets from ``.github/deploy_targets.yaml``, and
dispatches the payload to each target via
:func:`odp_releaser.github.send_dispatch`.

Every target is attempted regardless of earlier failures, a Markdown summary
of the outcomes is always written to the GitHub step summary, and the command
exits non-zero if any target failed.

Secrets (tokens and private keys) are never logged; only exception messages,
target ``owner``/``repo``/``event_type``, and the client payload (at debug
level) are logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import ruamel.yaml
import typer
from githubkit.exception import RequestFailed
from pydantic import TypeAdapter, ValidationError
from ruamel.yaml.error import YAMLError

from odp_releaser.bump_image_tester import (
    EventType,
    load_client_payload,
    prompt_event_type,
    prompt_image_name,
    set_payload_image,
)
from odp_releaser.cli_options import (
    GitHubActor,
    GitHubEventName,
    GitHubEventPath,
    GitHubRefName,
    GitHubRepository,
    GitHubRunId,
    GitHubServerUrl,
    GitHubSha,
    GitHubToken,
    ImageRepository,
)
from odp_releaser.github import (
    AppNotInstalledError,
    MissingCredentialsError,
    resolve_app_credentials,
    send_dispatch,
)
from odp_releaser.github_output import write_step_summary
from odp_releaser.logger import logger
from odp_releaser.make_payload import resolve_client_payload
from odp_releaser.schemas.dispatch import DeployTarget

_TARGETS_ADAPTER = TypeAdapter(list[DeployTarget])
DEFAULT_TARGETS_PATH = ".github/deploy_targets.yaml"

TargetsPath = Annotated[
    Path,
    typer.Option(
        envvar="DEPLOY_TARGETS_PATH",
        help="Path to the deploy targets YAML file",
    ),
]
DryRun = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        help="Resolve credentials for each target but send no dispatch events",
    ),
]


class InvalidDeployTargetsError(Exception):
    """The deploy targets file could not be parsed into deploy targets."""

    def __init__(self, targets_path: Path, detail: str) -> None:
        self.targets_path = targets_path
        super().__init__(
            f"{targets_path} is not a valid deploy-targets file "
            f"(expected a YAML array of {{owner, repo, event_type}}): {detail}"
        )


class MissingDeployTargetsError(Exception):
    """The deploy targets file does not exist."""

    def __init__(self, targets_path: Path) -> None:
        self.targets_path = targets_path
        super().__init__(
            f"No deploy targets file at {targets_path}. Generate one with "
            "`odp-releaser generate-config deploy-targets`."
        )


@dataclass
class TargetResult:
    """The outcome of attempting to dispatch to a single deploy target."""

    target: DeployTarget
    ok: bool
    detail: str


def load_targets(targets_path: Path) -> list[DeployTarget]:
    """Load and validate deploy targets from ``targets_path``.

    The file is parsed as YAML — a superset of JSON, so JSON files also load.
    Raises :class:`MissingDeployTargetsError` when the file does not exist.
    Returns an empty list when the file exists but is empty or an empty array.
    Raises :class:`InvalidDeployTargetsError` when the content is not valid
    YAML or does not match the :class:`DeployTarget` schema.
    """
    if not targets_path.exists():
        raise MissingDeployTargetsError(targets_path)

    content = targets_path.read_text().strip()
    if not content:
        return []

    yaml = ruamel.yaml.YAML(typ="safe", pure=True)
    try:
        data = yaml.load(content)
    except YAMLError as exc:
        raise InvalidDeployTargetsError(targets_path, str(exc)) from exc

    if data is None:
        return []

    try:
        targets = _TARGETS_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise InvalidDeployTargetsError(targets_path, str(exc)) from exc
    return targets


def _summary_table(results: list[TargetResult]) -> str:
    """Render the per-target outcomes as a Markdown table."""
    header = "| Target | Event type | Status | Detail |\n| --- | --- | --- | --- |"
    rows = [
        f"| {result.target.owner}/{result.target.repo} "
        f"| {result.target.event_type} "
        f"| {'OK' if result.ok else 'FAILED'} "
        f"| {result.detail} |"
        for result in results
    ]
    return "\n".join([header, *rows])


def notify(
    image_name: Annotated[str, typer.Argument(help="Name of the published image")],
    tag: Annotated[str, typer.Argument(help="Tag applied to the published image")],
    digest: Annotated[
        str, typer.Argument(help="Content digest of the published image")
    ],
    github_event_name: GitHubEventName,
    github_event_path: GitHubEventPath,
    github_repository: GitHubRepository,
    github_actor: GitHubActor,
    github_run_id: GitHubRunId,
    github_ref_name: GitHubRefName,
    github_sha: GitHubSha,
    image_repository: ImageRepository = None,
    github_token: GitHubToken = None,
    github_server_url: GitHubServerUrl = "https://github.com",
    targets_path: TargetsPath = Path(DEFAULT_TARGETS_PATH),
    *,
    dry_run: DryRun = False,
) -> None:
    """Dispatch the published image to every configured deploy target.

    Builds the ``client_payload`` from the GitHub Actions environment (the same
    payload ``make-payload`` prints), reads the deploy targets, and sends a
    ``repository_dispatch`` event to each one. All targets are attempted even
    if an earlier one fails, a Markdown summary is always written to the GitHub
    step summary, and the command exits non-zero if any target failed.

    With ``--dry-run`` credentials are resolved for each target but no tokens
    are minted and no dispatch events are sent.
    """
    # pylint: disable=duplicate-code
    payload = resolve_client_payload(
        image_name=image_name,
        tag=tag,
        digest=digest,
        github_event_name=github_event_name,
        github_event_path=github_event_path,
        github_repository=github_repository,
        github_actor=github_actor,
        github_run_id=github_run_id,
        github_ref_name=github_ref_name,
        github_sha=github_sha,
        image_repository=image_repository,
        github_token=github_token,
        github_server_url=github_server_url,
    )
    # pylint: enable=duplicate-code
    logger.debug("Client payload: %s", payload.model_dump_json())

    try:
        targets = load_targets(targets_path)
    except (MissingDeployTargetsError, InvalidDeployTargetsError) as exc:
        logger.error("%s", exc)
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if not targets:
        message = (
            f"No deploy targets configured in {targets_path}; nothing to dispatch."
        )
        logger.info(message)
        write_step_summary(message)
        return

    client_payload = payload.model_dump(mode="json")

    results: list[TargetResult] = []
    for target in targets:
        if dry_run:
            try:
                resolve_app_credentials(target.owner)
            except MissingCredentialsError as exc:
                results.append(TargetResult(target, ok=False, detail=str(exc)))
            else:
                results.append(
                    TargetResult(target, ok=True, detail="dry run (not sent)")
                )
            continue

        try:
            send_dispatch(target, client_payload)
        except (MissingCredentialsError, AppNotInstalledError, RequestFailed) as exc:
            results.append(TargetResult(target, ok=False, detail=str(exc)))
        else:
            results.append(TargetResult(target, ok=True, detail="dispatched"))

    table = _summary_table(results)
    write_step_summary(table)
    logger.info("Deploy dispatch summary:\n%s", table)

    if any(not result.ok for result in results):
        raise typer.Exit(1)


def test_notify(
    targets_path: TargetsPath = Path(DEFAULT_TARGETS_PATH),
    image_name: Annotated[
        str | None,
        typer.Option(
            "--image-name",
            help=(
                "Name of the image to substitute into the canned client "
                "payload. Prompted interactively when omitted."
            ),
        ),
    ] = None,
    event_type: Annotated[
        EventType | None,
        typer.Option(
            "--event-type",
            help=(
                "Canned client_payload event to load: push, release, or "
                "workflow_dispatch. Prompted interactively when omitted."
            ),
        ),
    ] = None,
) -> None:
    """Validate deploy targets and dispatch-credential availability.

    Builds a canned `client_payload` for the chosen event type (the same
    payloads `test bump-images` uses), reads the deploy targets, and reports,
    per target, whether dispatch app credentials are available. No tokens are
    minted and no network calls are made; this only checks whether
    `DISPATCH_APP_ID`/`DISPATCH_APP_PRIVATE_KEY` or `DISPATCH_APPS` cover
    every configured target owner.

    Missing credentials for a target are reported, not treated as a failure:
    the command exits non-zero only when `targets_path` does not exist or
    fails to parse as a valid deploy-targets file.
    """
    if image_name is None:
        image_name = prompt_image_name()
    if event_type is None:
        event_type = prompt_event_type()

    payload = load_client_payload(event_type)
    set_payload_image(image_name, payload)
    logger.info("Client payload: %s", payload.model_dump_json())

    try:
        targets = load_targets(targets_path)
    except (MissingDeployTargetsError, InvalidDeployTargetsError) as exc:
        logger.error("%s", exc)
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if not targets:
        message = f"No deploy targets configured in {targets_path}; nothing to test."
        logger.info(message)
        write_step_summary(message)
        return

    results: list[TargetResult] = []
    for target in targets:
        try:
            resolve_app_credentials(target.owner)
        except MissingCredentialsError:
            results.append(
                TargetResult(
                    target,
                    ok=False,
                    detail=(
                        "no credentials (set DISPATCH_APP_ID/"
                        "DISPATCH_APP_PRIVATE_KEY or DISPATCH_APPS)"
                    ),
                )
            )
        else:
            results.append(
                TargetResult(target, ok=True, detail="credentials available")
            )

    table = _summary_table(results)
    typer.echo(table)
    write_step_summary(table)
