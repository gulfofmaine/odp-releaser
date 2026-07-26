"""Comment back on the source pull request saying where an image went.

The human-readable counterpart to ``report-deployment``. That command records
a bump as a GitHub deployment on the source commit; this one posts (or updates)
a markdown comment on the source pull request naming the image, the tag, the
environment, and where the bump itself lives.

Runs in two places, from the same rendered templates:

- Right after a bump, from the ``bump-images`` step outputs. A
  ``pull_request``-mode bump is only *staged* — nothing is live until the bump
  pull request merges — so the ``staged`` template is used there and the
  ``deployed`` one for a direct commit.
- At merge time, from ``report-merged.yml``, with ``--pr-body``. Everything
  needed (templates, environment, source pull request number) was embedded in
  the bump pull request body at bump time, so the *staged* comment flips to
  *deployed* without the deploy repo ever being checked out.

Reaching the source repo needs reporter app credentials (``REPORTER_APPS`` /
``REPORTER_APP_ID`` / ``REPORTER_APP_PRIVATE_KEY``) whose app has been granted
``Pull requests: Read and write`` — a permission every existing installation
has to accept before it takes effect, which is why an ungranted mint is
reported with an actionable message rather than a traceback. The minted token
is scoped to the single source repository with ``pull_requests: write`` only,
and is never combined with the ``deployments: write`` mint
``report-deployment`` makes.

Secrets (tokens and private keys) are never logged.
"""

from __future__ import annotations

from typing import Annotated

import typer

from odp_releaser.cli_options import GitHubRepository
from odp_releaser.comment_body import (
    CommentState,
    build_context,
    comment_marker,
    render_comment,
)
from odp_releaser.github import upsert_pr_comment
from odp_releaser.github_output import write_step_summary
from odp_releaser.logger import logger
from odp_releaser.report_inputs import (
    REPORTING_ERRORS,
    reporter_token,
    resolve_source_inputs,
)
from odp_releaser.report_inputs import fail as _fail
from odp_releaser.report_inputs import skip as _skip
from odp_releaser.schemas.manifest_config import ResolvedComment

# snake_case, as the API body wants it -- NOT the hyphenated `pull-requests`
# spelling `actions/create-github-app-token` uses for its inputs.
COMMENT_TOKEN_PERMISSIONS = {"pull_requests": "write"}


def comment_on_pr(
    github_repository: GitHubRepository,
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
    *,
    pr_body: Annotated[
        str | None,
        typer.Option(
            envvar="PR_BODY",
            help=(
                "Body of a merged bump pull request; the payload, environment "
                "and comment templates embedded at bump time are read from it. "
                "Provide either this or the client payload."
            ),
        ),
    ] = None,
    update_mode: Annotated[
        str,
        typer.Option(
            envvar="UPDATE_MODE",
            help=(
                "How the bump landed (the `update_mode` output of "
                "`bump-images`): `commit` posts the deployed comment, "
                "`pull_request` posts the staged one"
            ),
        ),
    ] = "commit",
    environment: Annotated[
        str | None,
        typer.Option(
            envvar="ENVIRONMENT",
            help=(
                "GitHub environment name named in the comment, and part of the "
                "comment's identity. An environment embedded in --pr-body wins; "
                "unset falls back to the deploy repo's owner/name slug"
            ),
        ),
    ] = None,
    environment_url: Annotated[
        str | None,
        typer.Option(
            envvar="ENVIRONMENT_URL",
            help=(
                "Available to templates as `{environment_url}`. An URL embedded "
                "in --pr-body wins; unset falls back to --bump-url"
            ),
        ),
    ] = None,
    bump_url: Annotated[
        str | None,
        typer.Option(
            envvar="BUMP_URL",
            help=(
                "Where the bump itself lives — the bump commit or pull request "
                "URL — available to templates as `{bump_url}`"
            ),
        ),
    ] = None,
    run_url: Annotated[
        str | None,
        typer.Option(
            envvar="RUN_URL",
            help=(
                "This workflow run's URL, available to templates as "
                "`{run_url}`; defaults to the run the CLI is executing in"
            ),
        ),
    ] = None,
    pr_number: Annotated[
        int | None,
        typer.Option(
            envvar="COMMENT_PR_NUMBER",
            help=(
                "Source pull request to comment on (the `comment_pr_number` "
                "output of `bump-images`). Unset falls back to the payload's "
                "own pull request; no pull request at all is a no-op"
            ),
        ),
    ] = None,
    comment_enabled: Annotated[
        bool,
        typer.Option(
            envvar="COMMENT_ENABLED",
            help=(
                "Whether to comment at all (the `comment_enabled` output of "
                "`bump-images`). False is a no-op"
            ),
        ),
    ] = True,
    staged_template: Annotated[
        str | None,
        typer.Option(
            envvar="COMMENT_STAGED_TEMPLATE",
            help=(
                "Comment body used for a `pull_request`-mode bump (the "
                "`comment_staged_template` output of `bump-images`). A template "
                "embedded in --pr-body wins"
            ),
        ),
    ] = None,
    deployed_template: Annotated[
        str | None,
        typer.Option(
            envvar="COMMENT_DEPLOYED_TEMPLATE",
            help=(
                "Comment body used once the bump has landed (the "
                "`comment_deployed_template` output of `bump-images`). A "
                "template embedded in --pr-body wins"
            ),
        ),
    ] = None,
    github_server_url: Annotated[
        str,
        typer.Option(envvar="GITHUB_SERVER_URL", help="Base URL of the GitHub server"),
    ] = "https://github.com",
    github_run_id: Annotated[
        str | None,
        typer.Option(envvar="GITHUB_RUN_ID", help="ID of the workflow run"),
    ] = None,
) -> None:
    """Comment on the source pull request saying where an image was deployed.

    Posts the `staged` comment for a bump pull request awaiting review and the
    `deployed` one once the bump has landed, updating this deploy repo's
    existing comment for the image rather than adding another. Requires
    reporter app credentials with `Pull requests: Read and write`.
    """
    # Workflows plumb these through env vars, where "unset" arrives as "".
    client_payload = client_payload or None
    pr_body = pr_body or None
    environment = environment or None
    environment_url = environment_url or None
    bump_url = bump_url or None
    run_url = run_url or None
    staged_template = staged_template if staged_template != "" else None
    deployed_template = deployed_template if deployed_template != "" else None

    payload, metadata = resolve_source_inputs(client_payload, pr_body)
    if payload is None:
        _skip(
            "No odp-releaser report metadata found in the pull request "
            "body; nothing to comment on."
        )
        return

    comment: ResolvedComment | None = None
    if metadata is not None:
        # Values recorded at bump time carry the manifest config's intent, so
        # they win over the calling workflow's generic fallbacks.
        environment = metadata.environment or environment
        environment_url = metadata.environment_url or environment_url
        pr_number = metadata.comment_pr_number or pr_number
        comment = metadata.comment
        if comment is None:
            # A bump pull request opened by an older odp-releaser carries no
            # comment settings; inventing them here could post a comment the
            # config never asked for.
            _skip(
                "The pull request body carries no odp-releaser comment "
                "settings (opened by an older release); nothing to comment."
            )
            return
    else:
        comment = ResolvedComment(
            enabled=comment_enabled,
            staged=staged_template or "",
            deployed=deployed_template or "",
        )

    if not comment.enabled:
        _skip("Commenting is disabled for this image; nothing to comment.")
        return

    if pr_number is None and payload.source.pr is not None:
        pr_number = payload.source.pr.number
    if pr_number is None:
        _skip(
            f"The bump of {payload.image_name} has no source pull request to "
            "comment on (only push events carry one); nothing to comment."
        )
        return

    state = (
        CommentState.staged if update_mode == "pull_request" else CommentState.deployed
    )
    template = comment.staged if state is CommentState.staged else comment.deployed
    if not template:
        _skip(
            f"The {state.value} comment template is empty; nothing to comment "
            f"for {payload.image_name}."
        )
        return

    environment = environment or github_repository
    bump_url = bump_url or str(payload.source.url)
    if run_url is None:
        run_url = (
            f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"
            if github_run_id
            else bump_url
        )

    context = build_context(
        payload,
        deploy_repo=github_repository,
        environment=environment,
        environment_url=environment_url,
        update_mode=update_mode,
        bump_url=bump_url,
        run_url=run_url,
        state=state,
    )
    try:
        body = render_comment(template, context)
    except (KeyError, ValueError, IndexError) as exc:
        _fail(
            f"The {state.value} comment template could not be rendered: {exc}. "
            "Run `odp-releaser validate image-manifest` to check its "
            "placeholders."
        )

    try:
        token = reporter_token(payload.repo, permissions=COMMENT_TOKEN_PERMISSIONS)
        comment_url = upsert_pr_comment(
            payload.repo,
            pr_number,
            body,
            token,
            marker=comment_marker(github_repository, payload.image_name, environment),
        )
    except REPORTING_ERRORS as exc:
        _fail(f"Failed to comment on {payload.repo}#{pr_number}: {exc}")

    message = (
        f"Commented `{state.value}` bump of `{payload.image_name}:"
        f"{payload.new_tag()}` on `{payload.repo}`#{pr_number} "
        f"(environment `{environment}`): {comment_url}"
    )
    write_step_summary(message)
    logger.info(message)


if __name__ == "__main__":
    typer.run(comment_on_pr)
