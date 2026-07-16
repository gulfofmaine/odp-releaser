"""Typed wrapper over :mod:`githubkit` for the deployment-dispatch CLI.

This module is the only place in the package that talks to the GitHub REST
API. It resolves per-owner GitHub App credentials, mints installation access
tokens scoped to a single repository, sends ``repository_dispatch`` events,
and reports deployments back to source repos. Nothing outside this module
should touch HTTP directly.

Secrets (private keys and access tokens) are never logged.
"""

from __future__ import annotations

import os
from typing import Literal

from githubkit import AppAuthStrategy, GitHub, TokenAuthStrategy
from githubkit.exception import RequestFailed
from githubkit.utils import UNSET
from pydantic import TypeAdapter, ValidationError

from odp_releaser.logger import logger
from odp_releaser.schemas.dispatch import DeployTarget, DispatchAppCredentials
from odp_releaser.schemas.github_context import PrMerge

DISPATCH_APPS_ENV = "DISPATCH_APPS"
DISPATCH_APP_ID_ENV = "DISPATCH_APP_ID"
DISPATCH_APP_PRIVATE_KEY_ENV = "DISPATCH_APP_PRIVATE_KEY"

REPORTER_APPS_ENV = "REPORTER_APPS"
REPORTER_APP_ID_ENV = "REPORTER_APP_ID"
REPORTER_APP_PRIVATE_KEY_ENV = "REPORTER_APP_PRIVATE_KEY"

_APPS_ADAPTER = TypeAdapter(dict[str, DispatchAppCredentials])

DEFAULT_TOKEN_PERMISSIONS: dict[str, str] = {"contents": "write"}

# GitHub caps deployment and deployment-status descriptions at 140 characters.
DEPLOYMENT_DESCRIPTION_LIMIT = 140

DeploymentState = Literal[
    "error", "failure", "inactive", "in_progress", "queued", "pending", "success"
]


class MissingCredentialsError(Exception):
    """No app credentials could be resolved for an owner."""

    def __init__(
        self,
        owner: str,
        *,
        role: str = "dispatch",
        apps_env: str = DISPATCH_APPS_ENV,
        app_id_env: str = DISPATCH_APP_ID_ENV,
        private_key_env: str = DISPATCH_APP_PRIVATE_KEY_ENV,
    ) -> None:
        self.owner = owner
        super().__init__(
            f"No {role} app credentials for owner {owner!r}. "
            f"Add an entry for {owner!r} to {apps_env}, or set "
            f"{app_id_env} and {private_key_env} "
            f"for a default {role} app."
        )


class AppNotInstalledError(Exception):
    """The app is not installed on the target repository."""

    def __init__(self, owner: str, repo: str, *, role: str = "dispatch") -> None:
        self.owner = owner
        self.repo = repo
        super().__init__(
            f"The {role} app is not installed on {owner}/{repo}. "
            f"Install the {owner} org's {role} app on that repository first."
        )


def _resolve_credentials(
    owner: str,
    *,
    role: str,
    apps_env: str,
    app_id_env: str,
    private_key_env: str,
) -> DispatchAppCredentials:
    """Resolve app credentials for ``owner`` from an env-var triple.

    Prefers a per-owner entry in the ``apps_env`` JSON mapping, then falls
    back to the default ``app_id_env`` / ``private_key_env`` pair. Raises
    :class:`MissingCredentialsError` if neither is available.

    Environment is read at call time so tests can use ``monkeypatch``.
    """
    raw_apps = os.environ.get(apps_env)
    if raw_apps:
        try:
            apps = _APPS_ADAPTER.validate_json(raw_apps)
        except ValidationError as exc:
            msg = (
                f"{apps_env} is not a valid JSON mapping of "
                "owner to {app_id, private_key}: "
                f"{exc}"
            )
            raise ValueError(msg) from exc
        if owner in apps:
            logger.debug("Using %s credentials for owner %s", apps_env, owner)
            return apps[owner]

    app_id = os.environ.get(app_id_env)
    private_key = os.environ.get(private_key_env)
    if app_id and private_key:
        logger.debug("Using default %s app credentials for owner %s", role, owner)
        return DispatchAppCredentials(app_id=app_id, private_key=private_key)

    raise MissingCredentialsError(
        owner,
        role=role,
        apps_env=apps_env,
        app_id_env=app_id_env,
        private_key_env=private_key_env,
    )


def resolve_app_credentials(owner: str) -> DispatchAppCredentials:
    """Resolve dispatch app credentials for ``owner`` from the environment.

    Prefers a per-owner entry in the ``DISPATCH_APPS`` JSON mapping, then
    falls back to the default ``DISPATCH_APP_ID`` / ``DISPATCH_APP_PRIVATE_KEY``
    pair. Raises :class:`MissingCredentialsError` if neither is available.
    """
    return _resolve_credentials(
        owner,
        role="dispatch",
        apps_env=DISPATCH_APPS_ENV,
        app_id_env=DISPATCH_APP_ID_ENV,
        private_key_env=DISPATCH_APP_PRIVATE_KEY_ENV,
    )


def resolve_reporter_credentials(owner: str) -> DispatchAppCredentials:
    """Resolve reporter app credentials for ``owner`` from the environment.

    The reporter app is the source-org-owned mirror of the dispatch app: it is
    installed on source repos so a deploy repo can report deployments back.
    Prefers a per-owner entry in the ``REPORTER_APPS`` JSON mapping, then
    falls back to the default ``REPORTER_APP_ID`` / ``REPORTER_APP_PRIVATE_KEY``
    pair. Raises :class:`MissingCredentialsError` if neither is available.
    """
    return _resolve_credentials(
        owner,
        role="reporter",
        apps_env=REPORTER_APPS_ENV,
        app_id_env=REPORTER_APP_ID_ENV,
        private_key_env=REPORTER_APP_PRIVATE_KEY_ENV,
    )


def pr_for_commit(repo: str, sha: str, token: str) -> PrMerge | None:
    """Return the first pull request associated with ``sha`` in ``repo``.

    ``repo`` is a ``owner/name`` slug. Returns ``None`` when the commit is not
    associated with any pull request. ``token`` is passed explicitly by the
    caller (typically ``GITHUB_TOKEN``).
    """
    owner, _, name = repo.partition("/")
    with GitHub(TokenAuthStrategy(token)) as github:
        response = github.rest.repos.list_pull_requests_associated_with_commit(
            owner, name, sha
        )
    pulls = response.json()
    if not pulls:
        return None
    return PrMerge.model_validate(pulls[0])


def installation_token_for(
    creds: DispatchAppCredentials,
    owner: str,
    repo: str,
    *,
    permissions: dict[str, str] | None = None,
    role: str = "dispatch",
) -> str:
    """Mint an installation access token scoped to a single repository.

    Authenticates as the GitHub App, looks up its installation on
    ``owner/repo`` (raising :class:`AppNotInstalledError` on a 404), then
    creates an installation access token restricted to that repository with
    ``permissions`` (``contents: write`` by default). Returns the token
    string. ``role`` only labels error messages and logs.
    """
    if permissions is None:
        permissions = DEFAULT_TOKEN_PERMISSIONS
    with GitHub(AppAuthStrategy(creds.app_id, creds.private_key)) as github:
        try:
            installation = github.rest.apps.get_repo_installation(owner, repo)
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                raise AppNotInstalledError(owner, repo, role=role) from exc
            raise
        installation_id = installation.json()["id"]

        logger.debug(
            "Minting installation token for %s/%s (installation %s)",
            owner,
            repo,
            installation_id,
        )
        response = github.rest.apps.create_installation_access_token(
            installation_id,
            data={
                "repositories": [repo],
                "permissions": permissions,
            },
        )
    token: str = response.json()["token"]
    return token


def send_dispatch(target: DeployTarget, client_payload: dict[str, object]) -> None:
    """Send a ``repository_dispatch`` event to a deploy target.

    Resolves the target owner's dispatch app credentials, mints a token scoped
    to the target repository, and posts the dispatch event.
    """
    creds = resolve_app_credentials(target.owner)
    token = installation_token_for(creds, target.owner, target.repo)

    logger.debug(
        "Dispatching %s to %s/%s",
        target.event_type,
        target.owner,
        target.repo,
    )
    with GitHub(TokenAuthStrategy(token)) as github:
        github.rest.repos.create_dispatch_event(
            target.owner,
            target.repo,
            data={
                "event_type": target.event_type,
                "client_payload": client_payload,
            },
        )


def create_deployment(
    repo: str,
    *,
    ref: str,
    environment: str,
    description: str,
    token: str,
    payload: dict[str, str] | None = None,
) -> int:
    """Create a GitHub deployment on ``repo`` at ``ref`` and return its id.

    ``repo`` is an ``owner/name`` slug. ``auto_merge`` is always disabled (its
    API default would try to merge the default branch into ``ref``) and
    ``required_contexts`` is always empty (the source commit's own checks
    shouldn't block recording that it was deployed). ``payload`` is stored on
    the deployment for later inspection; it is never executed by GitHub.
    """
    owner, _, name = repo.partition("/")
    logger.debug("Creating deployment on %s at %s for %s", repo, ref, environment)
    with GitHub(TokenAuthStrategy(token)) as github:
        response = github.rest.repos.create_deployment(
            owner,
            name,
            ref=ref,
            environment=environment,
            description=description[:DEPLOYMENT_DESCRIPTION_LIMIT],
            auto_merge=False,
            required_contexts=[],
            payload=payload if payload is not None else UNSET,
        )
    deployment_id: int = response.json()["id"]
    return deployment_id


def list_deployments(repo: str, *, sha: str, environment: str, token: str) -> list[int]:
    """IDs of existing deployments on ``repo`` for ``sha`` + ``environment``.

    ``repo`` is an ``owner/name`` slug. Returns ids newest first (the API's
    default ordering), empty when nothing matches. Used to make reporting
    idempotent: a merge-time report finds the deployment the bump created and
    updates its status instead of piling up duplicates.
    """
    owner, _, name = repo.partition("/")
    with GitHub(TokenAuthStrategy(token)) as github:
        response = github.rest.repos.list_deployments(
            owner, name, sha=sha, environment=environment
        )
    return [deployment["id"] for deployment in response.json()]


def create_deployment_status(
    repo: str,
    deployment_id: int,
    state: DeploymentState,
    *,
    token: str,
    environment_url: str | None = None,
    log_url: str | None = None,
    description: str | None = None,
) -> None:
    """Set the status of a deployment previously created on ``repo``.

    ``repo`` is an ``owner/name`` slug. ``environment_url`` becomes the
    "View deployment" link and ``log_url`` the "deployment logs" link in the
    GitHub UI; both are optional.
    """
    owner, _, name = repo.partition("/")
    logger.debug(
        "Setting deployment %s on %s to %s",
        deployment_id,
        repo,
        state,
    )
    with GitHub(TokenAuthStrategy(token)) as github:
        github.rest.repos.create_deployment_status(
            owner,
            name,
            deployment_id,
            state=state,
            environment_url=environment_url or UNSET,
            log_url=log_url or UNSET,
            description=(
                description[:DEPLOYMENT_DESCRIPTION_LIMIT] if description else UNSET
            ),
        )


def upsert_pr_comment(repo: str, pr_number: int, body: str, token: str) -> None:
    """Create or update a bot comment on a pull request.

    Reserved for a future (v2) ``comment`` command that reports where images
    were deployed back onto the source pull request.
    """
    raise NotImplementedError
