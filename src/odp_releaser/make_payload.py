"""Build the ``repository_dispatch`` ``client_payload`` for a published image.

Runs as a step in a source repo's GitHub Actions workflow after an image has
been pushed to GHCR. It turns the GitHub Actions environment (plus, for
``release`` events, the event payload) into a
:class:`~odp_releaser.schemas.client_payload.ClientPayload` that a workflow
step can hand to ``gh api ... /dispatches`` (or ``odp-releaser``'s own
dispatch machinery) so deploy repos can react to the new image.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import HttpUrl, ValidationError

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
)
from odp_releaser.github import pr_for_commit
from odp_releaser.logger import logger
from odp_releaser.schemas.client_payload import (
    ClientPayload,
    ClientPayloadSource,
    PullRequest,
    Release,
)
from odp_releaser.schemas.github_context import PrMerge, ReleaseEvent

PUSH_EVENT = "push"
RELEASE_EVENT = "release"
WORKFLOW_DISPATCH_EVENT = "workflow_dispatch"

SUPPORTED_EVENTS = (PUSH_EVENT, RELEASE_EVENT, WORKFLOW_DISPATCH_EVENT)


class UnsupportedEventTypeError(ValueError):
    """Raised when ``make-payload`` is run for a GitHub event it can't handle."""

    def __init__(self, event_name: str) -> None:
        self.event_name = event_name
        super().__init__(
            f"Unsupported GitHub event {event_name!r}. make-payload only "
            f"supports {', '.join(SUPPORTED_EVENTS)} events."
        )


def build_payload(
    *,
    image_name: str,
    tag: str,
    digest: str,
    repo: str,
    actor: str,
    run_id: str,
    server_url: str,
    ref_name: str,
    sha: str,
    event_name: str,
    event_data: dict[str, Any],
    pr: PrMerge | None,
) -> ClientPayload:
    """Build the :class:`ClientPayload` for a published image.

    ``event_data`` is the parsed JSON of the event that triggered the
    workflow run (the contents of ``GITHUB_EVENT_PATH``). It is only
    consulted for ``release`` events; ``push`` and ``workflow_dispatch``
    don't need it and may be passed an empty ``dict``.

    ``pr`` is the pull request associated with the pushed commit (looked up
    via :func:`odp_releaser.github.pr_for_commit`), or ``None`` when there
    isn't one, a token wasn't available to look it up, or the event isn't a
    ``push``.

    Raises :class:`UnsupportedEventTypeError` for any event other than
    ``push``, ``release``, or ``workflow_dispatch``.
    """
    run_url = f"{server_url}/{repo}/actions/runs/{run_id}"
    image_ref = f"{image_name}@{digest}"

    release: Release | None = None
    pull_request: PullRequest | None = None

    if event_name == PUSH_EVENT:
        ref = ref_name
        if pr is not None:
            url = pr.html_url
            pull_request = PullRequest(
                number=pr.number, title=pr.title, url=HttpUrl(pr.html_url)
            )
        else:
            url = f"{server_url}/{repo}/commit/{sha}"
    elif event_name == RELEASE_EVENT:
        release_event = ReleaseEvent.model_validate(event_data)
        release_object = release_event.release
        ref = release_object.tag_name
        url = release_object.html_url
        release = Release(
            tag=release_object.tag_name,
            name=release_object.name or release_object.tag_name,
            url=HttpUrl(release_object.html_url),
        )
    elif event_name == WORKFLOW_DISPATCH_EVENT:
        ref = ref_name
        url = f"{server_url}/{repo}/commit/{sha}"
    else:
        raise UnsupportedEventTypeError(event_name)

    source = ClientPayloadSource(
        event=event_name,
        ref=ref,
        url=HttpUrl(url),
        run_url=HttpUrl(run_url),
        actor=actor,
        release=release,
        pr=pull_request,
    )

    return ClientPayload(
        image_name=image_name,
        digest=digest,
        tag=tag,
        git_sha=sha,
        image_ref=image_ref,
        source=source,
        repo=repo,
    )


def resolve_client_payload(
    *,
    image_name: str,
    tag: str,
    digest: str,
    github_event_name: str,
    github_event_path: Path,
    github_repository: str,
    github_actor: str,
    github_run_id: str,
    github_ref_name: str,
    github_sha: str,
    github_token: str | None,
    github_server_url: str,
) -> ClientPayload:
    """Turn the GitHub Actions environment into a :class:`ClientPayload`.

    Looks up the associated pull request for ``push`` events when a token is
    available, reads the event JSON from ``github_event_path`` for ``release``
    events, and delegates to :func:`build_payload`.

    ``pr_for_commit`` is referenced through this module so tests can
    ``monkeypatch`` ``odp_releaser.make_payload.pr_for_commit``.
    """
    pr: PrMerge | None = None
    if github_event_name == PUSH_EVENT:
        if github_token:
            pr = pr_for_commit(github_repository, github_sha, github_token)
        else:
            logger.warning(
                "No GITHUB_TOKEN available; dispatching push event without "
                "pull request information"
            )

    event_data: dict[str, Any] = {}
    if github_event_name == RELEASE_EVENT:
        event_data = json.loads(github_event_path.read_text())

    return build_payload(
        image_name=image_name,
        tag=tag,
        digest=digest,
        repo=github_repository,
        actor=github_actor,
        run_id=github_run_id,
        server_url=github_server_url,
        ref_name=github_ref_name,
        sha=github_sha,
        event_name=github_event_name,
        event_data=event_data,
        pr=pr,
    )


def make_payload(
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
    github_token: GitHubToken = None,
    github_server_url: GitHubServerUrl = "https://github.com",
) -> None:
    """Make a client payload for a repository_dispatch call for the given image and GitHub context.

    Reads the event JSON from ``GITHUB_EVENT_PATH`` when it's needed to build
    the payload (currently only for ``release`` events); ``push`` and
    ``workflow_dispatch`` events don't read it, so a missing or absent event
    file is fine for those. Prints ``payload.model_dump_json()`` to stdout so
    a workflow step can capture it, e.g. into ``CLIENT_PAYLOAD`` or straight
    into a ``gh api ... /dispatches`` call. All other logging goes to
    stderr.
    """
    # pylint: disable=duplicate-code
    try:
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
            github_token=github_token,
            github_server_url=github_server_url,
        )
    except ValidationError as exc:
        logger.error("%s", exc)
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    # pylint: enable=duplicate-code

    typer.echo(payload.model_dump_json())


if __name__ == "__main__":
    typer.run(make_payload)
