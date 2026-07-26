"""Shared input handling for the two commands that report back to a source repo.

``report-deployment`` and ``comment`` are deliberate mirrors: both run once
right after a bump (from a ``client_payload``) and again at merge time (from a
bump pull request's ``--pr-body``), both resolve the source owner's reporter
app credentials, and both must fail the same way on the same bad input. Keeping
that shared shape in one place means a fix to the "malformed metadata" path or
the token flow lands in both commands at once, rather than only in whichever
one someone happened to be editing.

Secrets (tokens and private keys) are never logged.
"""

from __future__ import annotations

import json
from typing import NoReturn

import typer
from githubkit.exception import RequestFailed
from pydantic import ValidationError

from odp_releaser.github import (
    AppNotInstalledError,
    MissingCredentialsError,
    PermissionsNotGrantedError,
    installation_token_for,
    resolve_reporter_credentials,
)
from odp_releaser.github_output import write_step_summary
from odp_releaser.logger import logger
from odp_releaser.report_metadata import ReportMetadata, extract_metadata
from odp_releaser.schemas.client_payload import ClientPayload

# Everything that means "the report could not be delivered": no credentials for
# the source owner, the app isn't installed there, it lacks a permission the
# token needs, or the API refused the call. Every one of them is reported as a
# best-effort failure rather than crashing the workflow step, so both commands
# catch exactly this set.
REPORTING_ERRORS = (
    MissingCredentialsError,
    AppNotInstalledError,
    PermissionsNotGrantedError,
    RequestFailed,
)


def fail(message: str) -> NoReturn:
    """Log, summarize, and echo ``message``, then exit non-zero."""
    logger.error(message)
    write_step_summary(message)
    typer.echo(message, err=True)
    raise typer.Exit(1)


def skip(message: str) -> None:
    """Log, summarize, and echo a friendly no-op."""
    logger.info(message)
    write_step_summary(message)
    typer.echo(message)


def resolve_source_inputs(
    client_payload: str | None, pr_body: str | None
) -> tuple[ClientPayload | None, ReportMetadata | None]:
    """Turn the "payload or pull request body" pair into a payload.

    Returns ``(payload, metadata)``. ``metadata`` is only set on the
    ``--pr-body`` path, and carries the environment, environment URL and
    comment settings recorded at bump time. A ``(None, None)`` result means the
    pull request body had no odp-releaser metadata at all — a friendly no-op the
    caller reports in its own words, since it's expected on any non-bump pull
    request.

    Exits non-zero when neither or both inputs are given, or when either is
    present but malformed.
    """
    if (client_payload is None) == (pr_body is None):
        fail(
            "Provide exactly one of the client payload (argument or "
            "CLIENT_PAYLOAD) or --pr-body."
        )

    if pr_body is not None:
        try:
            metadata = extract_metadata(pr_body)
        except (json.JSONDecodeError, ValidationError) as exc:
            fail(
                "Malformed odp-releaser report metadata in the pull request "
                f"body: {exc}"
            )
        if metadata is None:
            return None, None
        return metadata.client_payload, metadata

    assert client_payload is not None  # narrowed by the exactly-one check
    try:
        return ClientPayload.model_validate_json(client_payload), None
    except ValidationError as exc:
        logger.error("%s", exc)
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def reporter_token(repo: str, *, permissions: dict[str, str]) -> str:
    """Mint a reporter token scoped to the single source ``repo``.

    ``repo`` is an ``owner/name`` slug. ``permissions`` is requested exactly as
    given — each command asks only for what it needs (``deployments: write``
    for the deployment report, ``pull_requests: write`` for the comment) rather
    than one combined grant, so a source org that has only consented to one of
    them keeps working for that one.
    """
    owner, _, name = repo.partition("/")
    creds = resolve_reporter_credentials(owner)
    return installation_token_for(
        creds, owner, name, permissions=permissions, role="reporter"
    )
