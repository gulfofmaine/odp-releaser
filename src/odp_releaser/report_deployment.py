"""Report a completed image bump back to the source repo as a GitHub deployment.

Runs as a step in a deploy repo's ``bump-images`` workflow after the manifest
change has been committed or opened as a pull request. It creates a
[deployment](https://docs.github.com/en/rest/deployments/deployments) on the
**source** repository at the payload's ``git_sha`` and immediately sets its
status, so the source repo's pull request timeline and Environments sidebar
show where the image ended up.

The deployment state reflects what actually happened on the deploy side:
``success`` when the bump was committed directly (``update_mode: commit``),
``queued`` when a bump pull request was opened but not yet merged
(``update_mode: pull_request``).

Reaching the source repo requires the source org's **reporter app**
credentials (``REPORTER_APPS`` / ``REPORTER_APP_ID`` /
``REPORTER_APP_PRIVATE_KEY``) — see the GitHub Apps docs. The minted token is
scoped to the single source repository with ``deployments: write`` only.

Secrets (tokens and private keys) are never logged.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import ruamel.yaml
import typer
from githubkit.exception import RequestFailed
from pydantic import ValidationError

from odp_releaser.bump_images import DEFAULT_CONFIG_PATH
from odp_releaser.cli_options import GitHubRepository, GitHubRunId, GitHubServerUrl
from odp_releaser.github import (
    AppNotInstalledError,
    DeploymentState,
    MissingCredentialsError,
    create_deployment,
    create_deployment_status,
    installation_token_for,
    resolve_reporter_credentials,
)
from odp_releaser.github_output import write_step_summary
from odp_releaser.logger import logger
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import ManifestConfig


class UpdateMode(StrEnum):
    """How the bump landed in the deploy repo (``bump-images`` step output)."""

    commit = "commit"  # pylint: disable=invalid-name
    pull_request = "pull_request"  # pylint: disable=invalid-name


def report_deployment(
    client_payload: Annotated[
        str,
        typer.Argument(
            envvar="CLIENT_PAYLOAD",
            help="repository_dispatch client_payload string, can be loaded from env: `CLIENT_PAYLOAD`",
        ),
    ],
    github_repository: GitHubRepository,
    github_run_id: GitHubRunId,
    github_server_url: GitHubServerUrl = "https://github.com",
    *,
    update_mode: Annotated[
        UpdateMode,
        typer.Option(
            envvar="UPDATE_MODE",
            help=(
                "How the bump landed (the `update_mode` output of "
                "`bump-images`): `commit` reports a `success` deployment, "
                "`pull_request` reports a `queued` one"
            ),
        ),
    ] = UpdateMode.commit,
    environment_url: Annotated[
        str | None,
        typer.Option(
            envvar="ENVIRONMENT_URL",
            help=(
                "Shown as the 'View deployment' link on the source repo — "
                "typically the bump commit or pull request URL"
            ),
        ),
    ] = None,
    config_path: Annotated[
        Path,
        typer.Option(
            envvar="IMAGE_MANIFEST_CONFIG_PATH",
            help=(
                "Path to the image manifest configuration file, read for the "
                "optional `environment` name"
            ),
        ),
    ] = DEFAULT_CONFIG_PATH,
) -> None:
    """Report a completed bump to the source repo as a GitHub deployment.

    Creates a deployment on the source repository (from the payload's `repo`
    and `git_sha`) and sets its status: `success` for a direct commit,
    `queued` for a bump pull request that still needs review. Requires the
    source org's reporter app credentials in the environment.
    """
    try:
        payload = ClientPayload.model_validate_json(client_payload)
    except ValidationError as exc:
        logger.error("%s", exc)
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    environment = github_repository
    yaml = ruamel.yaml.YAML(typ="safe", pure=True)
    try:
        raw_config = config_path.read_text()  # pylint: disable=unspecified-encoding
        config = ManifestConfig.model_validate(yaml.load(raw_config))
    except FileNotFoundError:
        logger.debug(
            "No manifest config at %s; using %s as the environment name",
            config_path,
            environment,
        )
    else:
        if config.environment:
            environment = config.environment

    owner, _, name = payload.repo.partition("/")
    state: DeploymentState = (
        "success" if update_mode is UpdateMode.commit else "queued"
    )
    log_url = f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"
    description = f"{payload.image_name}:{payload.new_tag()} in {github_repository}"

    try:
        creds = resolve_reporter_credentials(owner)
        token = installation_token_for(
            creds,
            owner,
            name,
            permissions={"deployments": "write"},
            role="reporter",
        )
        deployment_id = create_deployment(
            payload.repo,
            ref=payload.git_sha,
            environment=environment,
            description=description,
            token=token,
            payload={
                "image_ref": payload.image_ref,
                "deploy_repo": github_repository,
            },
        )
        create_deployment_status(
            payload.repo,
            deployment_id,
            state,
            token=token,
            environment_url=environment_url,
            log_url=log_url,
            description=description,
        )
    except (MissingCredentialsError, AppNotInstalledError, RequestFailed) as exc:
        message = f"Failed to report deployment to {payload.repo}: {exc}"
        logger.error(message)
        write_step_summary(message)
        typer.echo(message, err=True)
        raise typer.Exit(1) from exc

    message = (
        f"Reported `{state}` deployment of `{payload.image_name}:"
        f"{payload.new_tag()}` to `{payload.repo}` "
        f"(environment `{environment}`)"
    )
    write_step_summary(message)
    logger.info(message)
