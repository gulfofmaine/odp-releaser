"""Typed wrapper over :mod:`githubkit` for the deployment-dispatch CLI.

This module is the only place in the package that talks to the GitHub REST
API. It resolves per-owner GitHub App credentials, mints installation access
tokens scoped to a single repository, and sends ``repository_dispatch``
events. Nothing outside this module should touch HTTP directly.

Secrets (private keys and access tokens) are never logged.
"""

from __future__ import annotations

import os

from githubkit import AppAuthStrategy, GitHub, TokenAuthStrategy
from githubkit.exception import RequestFailed
from pydantic import TypeAdapter, ValidationError

from odp_releaser.logger import logger
from odp_releaser.schemas.dispatch import DeployTarget, DispatchAppCredentials
from odp_releaser.schemas.github_context import PrMerge

DISPATCH_APPS_ENV = "DISPATCH_APPS"
DISPATCH_APP_ID_ENV = "DISPATCH_APP_ID"
DISPATCH_APP_PRIVATE_KEY_ENV = "DISPATCH_APP_PRIVATE_KEY"

_DISPATCH_APPS_ADAPTER = TypeAdapter(dict[str, DispatchAppCredentials])


class MissingCredentialsError(Exception):
    """No dispatch app credentials could be resolved for an owner."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        super().__init__(
            f"No dispatch app credentials for owner {owner!r}. "
            f"Add an entry for {owner!r} to {DISPATCH_APPS_ENV}, or set "
            f"{DISPATCH_APP_ID_ENV} and {DISPATCH_APP_PRIVATE_KEY_ENV} "
            "for a default dispatch app."
        )


class AppNotInstalledError(Exception):
    """The dispatch app is not installed on the target repository."""

    def __init__(self, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo
        super().__init__(
            f"The dispatch app is not installed on {owner}/{repo}. "
            f"Install the {owner} org's dispatch app on that repository "
            "before sending a deployment dispatch."
        )


def resolve_app_credentials(owner: str) -> DispatchAppCredentials:
    """Resolve dispatch app credentials for ``owner`` from the environment.

    Prefers a per-owner entry in the ``DISPATCH_APPS`` JSON mapping, then
    falls back to the default ``DISPATCH_APP_ID`` / ``DISPATCH_APP_PRIVATE_KEY``
    pair. Raises :class:`MissingCredentialsError` if neither is available.

    Environment is read at call time so tests can use ``monkeypatch``.
    """
    raw_apps = os.environ.get(DISPATCH_APPS_ENV)
    if raw_apps:
        try:
            apps = _DISPATCH_APPS_ADAPTER.validate_json(raw_apps)
        except ValidationError as exc:
            msg = (
                f"{DISPATCH_APPS_ENV} is not a valid JSON mapping of "
                "owner to {app_id, private_key}: "
                f"{exc}"
            )
            raise ValueError(msg) from exc
        if owner in apps:
            logger.debug("Using %s credentials for owner %s", DISPATCH_APPS_ENV, owner)
            return apps[owner]

    app_id = os.environ.get(DISPATCH_APP_ID_ENV)
    private_key = os.environ.get(DISPATCH_APP_PRIVATE_KEY_ENV)
    if app_id and private_key:
        logger.debug("Using default dispatch app credentials for owner %s", owner)
        return DispatchAppCredentials(app_id=app_id, private_key=private_key)

    raise MissingCredentialsError(owner)


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


def installation_token_for(creds: DispatchAppCredentials, owner: str, repo: str) -> str:
    """Mint an installation access token scoped to a single repository.

    Authenticates as the GitHub App, looks up its installation on
    ``owner/repo`` (raising :class:`AppNotInstalledError` on a 404), then
    creates an installation access token restricted to that repository with
    ``contents: write`` permission. Returns the token string.
    """
    with GitHub(AppAuthStrategy(creds.app_id, creds.private_key)) as github:
        try:
            installation = github.rest.apps.get_repo_installation(owner, repo)
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                raise AppNotInstalledError(owner, repo) from exc
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
                "permissions": {"contents": "write"},
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


def upsert_pr_comment(repo: str, pr_number: int, body: str, token: str) -> None:
    """Create or update a bot comment on a pull request.

    Reserved for a future (v2) ``comment`` command that reports where images
    were deployed back onto the source pull request.
    """
    raise NotImplementedError
