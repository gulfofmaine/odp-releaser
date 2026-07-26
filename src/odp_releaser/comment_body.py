"""Render the comment a bump posts back onto the source pull request.

``report-deployment`` already records *that* an image landed, as a GitHub
deployment on the source commit. This module builds the human-readable other
half: a markdown comment on the source pull request naming the image, the tag,
the environment, and where the bump itself lives — reading *staged* while a
bump pull request is still awaiting review, and *deployed* once it has landed.

Two pieces travel together here:

- :class:`CommentContext` is the entire vocabulary a comment template may
  reference. :data:`COMMENT_TEMPLATE_KEYS` is derived from its fields, so the
  validator's idea of a valid placeholder cannot drift from what rendering
  actually supplies. It is deliberately *separate* from
  ``ClientPayload.value_format_kwargs()`` (the vocabulary for ``set`` and
  ``environment_url`` templates): those describe a value written into a
  manifest and must stay narrow, while a comment also wants run facts the
  payload knows nothing about — which deploy repo bumped it, which environment
  it resolved to, the URL of the bump commit or pull request.

- :func:`comment_marker` is the comment's identity. An invisible HTML comment
  keyed on ``(deploy repo, image, environment)`` is appended to every rendered
  body so a later run can find and update *its own* comment rather than
  posting a duplicate — and so sibling bumps (a second image, a second deploy
  repo) never overwrite each other on the same source pull request.

Templates are rendered with ``str.format``, matching the ``set``/
``environment_url`` templates elsewhere in the config. Markdown that needs a
literal brace — a Helm or Go template inside a fence, a ``${{ }}`` expression —
must double it (``{{``/``}}``); ``odp-releaser validate image-manifest``
reports the failure ahead of time when it doesn't.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from odp_releaser.schemas.client_payload import ClientPayload

MARKER_PREFIX = "<!-- odp-releaser:comment key="

# Obviously-synthetic placeholder values for :func:`synthesize_context`, in the
# same spirit as ``validation.engine_backstop``'s synthetic payload. ``.invalid``
# is the reserved (RFC 2606) TLD for names guaranteed never to resolve.
_SYNTHETIC_DIGEST = "sha256:" + "0" * 64
_SYNTHETIC_GIT_SHA = "0" * 40
_SYNTHETIC_URL = "https://example.invalid/odp-releaser-synthetic-validation"


class CommentState(StrEnum):
    """Which lifecycle state a comment describes."""

    staged = "staged"  # pylint: disable=invalid-name
    deployed = "deployed"  # pylint: disable=invalid-name


class CommentContext(BaseModel):
    """Every fact a comment template may reference.

    Field names are the placeholder names, and every value is a plain string so
    a template can never trip over an ``int`` or an ``HttpUrl`` in a format
    spec. Adding a field here adds a placeholder everywhere — the validator
    derives its vocabulary from this model.
    """

    # From the client payload.
    image_name: str
    image_ref: str
    new_tag: str
    digest: str
    git_sha: str
    source_repo: str
    source_url: str
    actor: str
    # From the deploy-side run.
    deploy_repo: str
    environment: str
    environment_url: str
    update_mode: str
    bump_url: str
    run_url: str
    state: str

    def format_kwargs(self) -> dict[str, str]:
        """Keyword arguments for ``template.format(**kwargs)``."""
        return self.model_dump()


COMMENT_TEMPLATE_KEYS: frozenset[str] = frozenset(CommentContext.model_fields)


def build_context(
    payload: ClientPayload,
    *,
    deploy_repo: str,
    environment: str,
    environment_url: str | None,
    update_mode: str,
    bump_url: str,
    run_url: str,
    state: CommentState,
) -> CommentContext:
    """Assemble the render context from a payload plus this run's facts.

    ``environment_url`` falls back to ``bump_url`` when the image manifest
    doesn't configure one: an unset value would otherwise render as an empty
    markdown link target in any template that uses it.
    """
    return CommentContext(
        image_name=payload.image_name,
        image_ref=payload.image_ref,
        new_tag=payload.new_tag(),
        digest=payload.digest,
        git_sha=payload.git_sha,
        source_repo=payload.repo,
        source_url=str(payload.source.url),
        actor=payload.source.actor,
        deploy_repo=deploy_repo,
        environment=environment,
        environment_url=environment_url or bump_url,
        update_mode=update_mode,
        bump_url=bump_url,
        run_url=run_url,
        state=state.value,
    )


def comment_marker(deploy_repo: str, image_name: str, environment: str) -> str:
    """The invisible marker identifying one deploy repo's comment for one image.

    Keyed on all three because a single source pull request can receive
    dispatches for several images, from several deploy repos, into several
    environments — each of which owns a separate comment. The key is only ever
    matched as a substring, never parsed back out.
    """
    return f"{MARKER_PREFIX}{deploy_repo}|{image_name}|{environment} -->"


def render_comment(template: str, context: CommentContext) -> str:
    """Render ``template`` against ``context`` and append its marker.

    Raises ``KeyError``/``ValueError`` for a template the validator should have
    rejected (unknown placeholder, stray brace) rather than papering over it.
    """
    body = template.format(**context.format_kwargs())
    marker = comment_marker(
        context.deploy_repo, context.image_name, context.environment
    )
    return f"{body}\n\n{marker}"


def synthesize_context() -> CommentContext:
    """A placeholder context for validating templates with no bump in flight.

    ``odp-releaser validate image-manifest`` has no real payload or run to draw
    on, but still wants to attempt the actual ``.format`` call rather than only
    checking placeholder names — the same reasoning (and the same
    obviously-synthetic values) as
    ``validation.engine_backstop.synthesize_payload``. Every field is non-empty
    so a template that only *renders* badly with an empty value isn't reported
    as broken here.
    """
    return CommentContext(
        image_name="odp-releaser/synthetic-image",
        image_ref=f"odp-releaser/synthetic-image@{_SYNTHETIC_DIGEST}",
        new_tag="synthetic",
        digest=_SYNTHETIC_DIGEST,
        git_sha=_SYNTHETIC_GIT_SHA,
        source_repo="odp-releaser/synthetic-validation",
        source_url=_SYNTHETIC_URL,
        actor="odp-releaser-validator",
        deploy_repo="odp-releaser/synthetic-deploy",
        environment="synthetic",
        environment_url=_SYNTHETIC_URL,
        update_mode="commit",
        bump_url=_SYNTHETIC_URL,
        run_url=_SYNTHETIC_URL,
        state=CommentState.deployed.value,
    )
