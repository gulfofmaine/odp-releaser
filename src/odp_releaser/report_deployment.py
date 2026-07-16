"""Report a completed image bump back to the source repo as a GitHub deployment.

Runs after a bump has landed in a deploy repo — as the final step of the
``bump-images`` workflow, or from a merge-time workflow once a bump pull
request merges. It creates (or finds) a
[deployment](https://docs.github.com/en/rest/deployments/deployments) on the
**source** repository at the payload's ``git_sha`` and sets its status, so
the source repo's pull request timeline and Environments sidebar show where
the image ended up.

The deployment state reflects what actually happened on the deploy side:
``success`` when the bump was committed directly (``update_mode: commit``),
``queued`` when a bump pull request was opened but not yet merged
(``update_mode: pull_request``). Reporting is idempotent: an existing
deployment for the same commit + environment is reused, which is how a
merge-time ``--pr-body`` run flips the bump PR's ``queued`` deployment to
``success`` instead of creating a duplicate.

Reaching the source repo requires reporter app credentials (``REPORTER_APPS``
/ ``REPORTER_APP_ID`` / ``REPORTER_APP_PRIVATE_KEY``) — see the GitHub Apps
docs. The minted token is scoped to the single source repository with
``deployments: write`` only.

Secrets (tokens and private keys) are never logged.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, NoReturn

import typer
from githubkit.exception import RequestFailed
from pydantic import ValidationError

from odp_releaser.cli_options import GitHubRepository, GitHubRunId, GitHubServerUrl
from odp_releaser.github import (
    AppNotInstalledError,
    DeploymentState,
    MissingCredentialsError,
    create_deployment,
    create_deployment_status,
    installation_token_for,
    list_deployments,
    resolve_reporter_credentials,
)
from odp_releaser.github_output import write_step_summary
from odp_releaser.logger import logger
from odp_releaser.report_metadata import extract_metadata
from odp_releaser.schemas.client_payload import ClientPayload


class UpdateMode(StrEnum):
    """How the bump landed in the deploy repo (``bump-images`` step output)."""

    commit = "commit"  # pylint: disable=invalid-name
    pull_request = "pull_request"  # pylint: disable=invalid-name


def _fail(message: str) -> NoReturn:
    """Log, summarize, and echo ``message``, then exit non-zero."""
    logger.error(message)
    write_step_summary(message)
    typer.echo(message, err=True)
    raise typer.Exit(1)


def report_deployment(
    github_repository: GitHubRepository,
    github_run_id: GitHubRunId,
    client_payload: Annotated[
        str | None,
        typer.Argument(
            envvar="CLIENT_PAYLOAD",
            help=(
                "repository_dispatch client_payload string, can be loaded "
                "from env: `CLIENT_PAYLOAD`. Provide either this or --pr-body."
            ),
        ),
    ] = None,
    github_server_url: GitHubServerUrl = "https://github.com",
    *,
    pr_body: Annotated[
        str | None,
        typer.Option(
            envvar="PR_BODY",
            help=(
                "Body of a merged bump pull request; the payload, environment "
                "and environment URL embedded at bump time are read from it. "
                "Provide either this or the client payload."
            ),
        ),
    ] = None,
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
    environment: Annotated[
        str | None,
        typer.Option(
            envvar="ENVIRONMENT",
            help=(
                "GitHub environment name for the deployment. An environment "
                "embedded in --pr-body wins; unset falls back to the deploy "
                "repo's owner/name slug"
            ),
        ),
    ] = None,
    environment_url: Annotated[
        str | None,
        typer.Option(
            envvar="ENVIRONMENT_URL",
            help=(
                "Shown as the 'View deployment' link on the source repo — "
                "typically the bump commit or pull request URL. An URL "
                "embedded in --pr-body wins."
            ),
        ),
    ] = None,
) -> None:
    """Report a completed bump to the source repo as a GitHub deployment.

    Creates a deployment on the source repository (from the payload's `repo`
    and `git_sha`) and sets its status: `success` for a direct commit,
    `queued` for a bump pull request that still needs review. An existing
    deployment for the same commit and environment is reused, so running
    again after the bump PR merges flips its status instead of duplicating
    it. Requires reporter app credentials in the environment.
    """
    # Workflows plumb these through env vars, where "unset" arrives as "".
    client_payload = client_payload or None
    pr_body = pr_body or None
    environment = environment or None
    environment_url = environment_url or None

    if (client_payload is None) == (pr_body is None):
        _fail(
            "Provide exactly one of the client payload (argument or "
            "CLIENT_PAYLOAD) or --pr-body."
        )

    if pr_body is not None:
        try:
            metadata = extract_metadata(pr_body)
        except (json.JSONDecodeError, ValidationError) as exc:
            _fail(
                "Malformed odp-releaser report metadata in the pull request "
                f"body: {exc}"
            )
        if metadata is None:
            message = (
                "No odp-releaser report metadata found in the pull request "
                "body; nothing to report."
            )
            logger.info(message)
            write_step_summary(message)
            typer.echo(message)
            return
        payload = metadata.client_payload
        # Values recorded at bump time carry the manifest config's intent, so
        # they win over the calling workflow's generic fallbacks.
        environment = metadata.environment or environment
        environment_url = metadata.environment_url or environment_url
    else:
        assert client_payload is not None  # narrowed by the exactly-one check
        try:
            payload = ClientPayload.model_validate_json(client_payload)
        except ValidationError as exc:
            logger.error("%s", exc)
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    environment = environment or github_repository
    owner, _, name = payload.repo.partition("/")
    state: DeploymentState = "success" if update_mode is UpdateMode.commit else "queued"
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
        existing = list_deployments(
            payload.repo,
            sha=payload.git_sha,
            environment=environment,
            token=token,
        )
        if existing:
            deployment_id = existing[0]
            logger.info(
                "Reusing existing deployment %s on %s for %s",
                deployment_id,
                payload.repo,
                environment,
            )
        else:
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
        _fail(f"Failed to report deployment to {payload.repo}: {exc}")

    message = (
        f"Reported `{state}` deployment of `{payload.image_name}:"
        f"{payload.new_tag()}` to `{payload.repo}` "
        f"(environment `{environment}`)"
    )
    write_step_summary(message)
    logger.info(message)
