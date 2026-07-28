"""Typed wrapper over :mod:`githubkit` for the deployment-dispatch CLI.

This module is the only place in the package that talks to the GitHub REST
API. It resolves per-owner GitHub App credentials, mints installation access
tokens scoped to a single repository, sends ``repository_dispatch`` events,
and reports back to source repos — as deployments, and as a comment on the
source pull request. Nothing outside this module should touch HTTP directly.

Secrets (private keys and access tokens) are never logged.
"""

from __future__ import annotations

import os
from typing import Literal

from githubkit import AppAuthStrategy, GitHub, TokenAuthStrategy
from githubkit.exception import RequestFailed
from githubkit.utils import UNSET
from githubkit_schemas.latest.models import AppPermissions
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

# The permission names GitHub's app-permissions object accepts, read off
# githubkit's model rather than restated. Worth checking against, because a
# misspelled name is *silently dropped* on the way out — and a token request
# with no permissions at all is granted every permission the installation
# holds. `pull_requests` is a live trap here: the hyphenated `pull-requests`
# spelling is what `actions/create-github-app-token`'s inputs use, so it reads
# as correct.
KNOWN_TOKEN_PERMISSIONS: frozenset[str] = frozenset(AppPermissions.model_fields)

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


class AppNotInstalledOnOrgError(Exception):
    """The app has no installation on the target organization."""

    def __init__(self, org: str, *, role: str = "reporter") -> None:
        self.org = org
        super().__init__(
            f"The {role} app is not installed on the {org} organization. "
            f"Install the {org} org's {role} app first."
        )


class PermissionsNotGrantedError(Exception):
    """The app is installed, but was never granted a requested permission.

    GitHub refuses to mint an installation token with permissions broader than
    the app itself holds (422; "The installation access token cannot be granted
    permissions that the app was not granted"). The common cause is a *new*
    permission added to an existing app: every installation has to accept the
    updated permission request before tokens can carry it.

    Raised only for a 422 whose message actually names permissions — see
    :func:`_is_permissions_error`, since the same status code covers unrelated
    problems.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        permissions: dict[str, str],
        *,
        role: str = "reporter",
        detail: str = "",
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.permissions = permissions
        self.detail = detail
        requested = ", ".join(
            f"{name}: {level}" for name, level in sorted(permissions.items())
        )
        suffix = f" GitHub said: {detail}" if detail else ""
        super().__init__(
            f"The {role} app on {owner}/{repo} has not been granted the "
            f"permissions this token needs ({requested}). Add them to the app, "
            f"then have {owner} accept the permission request GitHub sends for "
            "the existing installation — a newly requested permission stays "
            f"inactive until the installation owner accepts it.{suffix}"
        )


def response_message(exc: RequestFailed) -> str:
    """GitHub's own ``message`` for a failed request, or ``""``.

    Defensive: an error response isn't guaranteed to be JSON, or to carry a
    ``message`` at all.
    """
    try:
        body = exc.response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str):
            return message
    return ""


def describe_request_failure(exc: RequestFailed) -> str:
    """A reportable one-liner for a ``RequestFailed``.

    ``str(RequestFailed)`` is githubkit's repr of the response object
    (``Response(422 Unprocessable Entity, data_model=...)``), which tells a
    workflow log nothing about what GitHub objected to. This puts the status and
    GitHub's own message in front of whoever has to fix it.
    """
    status = exc.response.status_code
    message = response_message(exc)
    return (
        f"GitHub returned {status}: {message}"
        if message
        else f"GitHub returned {status}"
    )


def _is_permissions_error(exc: RequestFailed) -> bool:
    """Whether a 422 from the token mint is about ungranted permissions.

    ``create_installation_access_token`` answers 422 for more than one reason —
    notably a ``repositories`` entry that doesn't exist or isn't accessible to
    the installation, which happens when a source repo is renamed or
    transferred. Telling someone to go accept a permission request they already
    accepted would send them the wrong way, so the specific advice is gated on
    GitHub's message mentioning permissions at all. Anything else stays a plain
    ``RequestFailed``, which callers already report with GitHub's own wording —
    so a reworded message degrades to the raw error rather than to bad advice.
    """
    return "permission" in response_message(exc).lower()


def _check_permissions(permissions: dict[str, str]) -> None:
    """Reject permission names GitHub wouldn't recognize.

    A misspelled name is dropped rather than rejected by the API, and a token
    minted with an empty permissions object carries *every* permission the
    installation has — so a typo silently over-grants instead of failing. This
    turns that into a loud error before the request is made.
    """
    unknown = sorted(set(permissions) - KNOWN_TOKEN_PERMISSIONS)
    if unknown:
        msg = (
            f"Unknown installation token permission(s): {', '.join(unknown)}. "
            "GitHub silently ignores unrecognized names, and a token with no "
            "permissions receives all of the installation's, so this is "
            "rejected here. Names are snake_case (e.g. 'pull_requests', not "
            "'pull-requests')."
        )
        raise ValueError(msg)


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


def is_team_member(org: str, team_slug: str, username: str, token: str) -> bool:
    """Whether ``username`` is an active member of ``org``'s ``team_slug`` team.

    Returns ``False`` when the user has no membership (404) or the membership
    is still ``pending``. ``token`` needs organization members read access —
    the default workflow ``GITHUB_TOKEN`` cannot read team membership. Other
    request failures (bad credentials, missing scope, unknown team) propagate
    so callers can distinguish "not a member" from "could not check".
    """
    with GitHub(TokenAuthStrategy(token)) as github:
        try:
            response = github.rest.teams.get_membership_for_user_in_org(
                org, team_slug, username
            )
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                return False
            raise
    state: str = response.json()["state"]
    return state == "active"


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

    Asking for a permission the app doesn't hold is a 422 rather than a
    silently narrower token, and is raised as
    :class:`PermissionsNotGrantedError` — the expected failure while a source
    org has yet to accept a newly added permission. Other 422s (an
    inaccessible or renamed repository, say) stay plain ``RequestFailed``; see
    :func:`_is_permissions_error`.
    """
    if permissions is None:
        permissions = DEFAULT_TOKEN_PERMISSIONS
    _check_permissions(permissions)
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
        try:
            response = github.rest.apps.create_installation_access_token(
                installation_id,
                data={
                    "repositories": [repo],
                    "permissions": permissions,
                },
            )
        except RequestFailed as exc:
            if exc.response.status_code == 422 and _is_permissions_error(exc):
                raise PermissionsNotGrantedError(
                    owner,
                    repo,
                    permissions,
                    role=role,
                    detail=response_message(exc),
                ) from exc
            raise
        token: str = response.json()["token"]
    return token


def org_installation_token_for(
    creds: DispatchAppCredentials,
    org: str,
    *,
    permissions: dict[str, str],
    role: str = "reporter",
) -> str:
    """Mint an installation access token from an app's organization installation.

    Authenticates as the GitHub App, looks up its installation on the ``org``
    organization (raising :class:`AppNotInstalledOnOrgError` on a 404), then
    creates an installation access token restricted to ``permissions``.
    Organization permissions (e.g. ``members: read``) are only honored when
    the app itself has been granted them. Returns the token string. ``role``
    only labels error messages and logs.
    """
    _check_permissions(permissions)
    with GitHub(AppAuthStrategy(creds.app_id, creds.private_key)) as github:
        try:
            installation = github.rest.apps.get_org_installation(org)
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                raise AppNotInstalledOnOrgError(org, role=role) from exc
            raise
        installation_id = installation.json()["id"]

        logger.debug(
            "Minting org installation token for %s (installation %s)",
            org,
            installation_id,
        )
        response = github.rest.apps.create_installation_access_token(
            installation_id,
            data={"permissions": permissions},
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


def upsert_pr_comment(
    repo: str, pr_number: int, body: str, token: str, *, marker: str
) -> str:
    """Create or update this deploy repo's comment on a pull request.

    ``repo`` is an ``owner/name`` slug. ``marker`` is the invisible key
    :func:`odp_releaser.comment_body.comment_marker` embeds in ``body``: the
    first existing comment containing it is updated in place, and a new comment
    is posted when none does. That keeps reruns idempotent while leaving other
    deploy repos' (and other images') comments untouched.

    Every comment on the pull request is paged through because the issue
    comments endpoint offers no sort or filter — a marker sitting on a later
    page of a busy pull request would otherwise be missed and duplicated.
    Returns the comment's ``html_url``.

    Needs a token with ``pull_requests: write``; a pull request that can't be
    commented on (locked, archived repository) surfaces as ``RequestFailed``
    for the caller to report.
    """
    owner, _, name = repo.partition("/")
    with GitHub(TokenAuthStrategy(token)) as github:
        existing_id: int | None = None
        for comment in github.rest.paginate(
            github.rest.issues.list_comments,
            # Raw JSON rather than parsed models, like `list_deployments` and
            # `pr_for_commit`: only `id` and `body` are needed, and the full
            # IssueComment model would make every caller (and every test
            # fixture) supply fields this has no use for.
            lambda response: response.json(),
            owner=owner,
            repo=name,
            issue_number=pr_number,
        ):
            if marker in (comment.get("body") or ""):
                existing_id = comment["id"]
                break

        if existing_id is not None:
            logger.debug("Updating comment %s on %s#%s", existing_id, repo, pr_number)
            response = github.rest.issues.update_comment(
                owner, name, existing_id, data={"body": body}
            )
        else:
            logger.debug("Creating a comment on %s#%s", repo, pr_number)
            response = github.rest.issues.create_comment(
                owner, name, pr_number, data={"body": body}
            )
        comment_url: str = response.json()["html_url"]
    return comment_url
