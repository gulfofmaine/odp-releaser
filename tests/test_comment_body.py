"""Rendering and identity for source-pull-request comments."""

from __future__ import annotations

import pytest

from odp_releaser.comment_body import (
    COMMENT_TEMPLATE_KEYS,
    CommentContext,
    CommentState,
    build_context,
    comment_marker,
    render_comment,
    synthesize_context,
)
from odp_releaser.schemas.client_payload import (
    ClientPayload,
    ClientPayloadSource,
    PullRequest,
)
from odp_releaser.schemas.manifest_config import (
    DEFAULT_DEPLOYED_TEMPLATE,
    DEFAULT_STAGED_TEMPLATE,
)

DIGEST = "sha256:" + "ab" * 32


def _payload(*, event: str = "push", with_pr: bool = True) -> ClientPayload:
    return ClientPayload(
        image_name="ghcr.io/acme/widget",
        digest=DIGEST,
        tag="v1.2.3",
        git_sha="c" * 40,
        image_ref=f"ghcr.io/acme/widget@{DIGEST}",
        repo="acme/widget-src",
        source=ClientPayloadSource(
            event=event,
            ref="main",
            url="https://github.com/acme/widget-src/pull/7",  # type: ignore[arg-type]
            run_url="https://github.com/acme/widget-src/actions/runs/1",  # type: ignore[arg-type]
            actor="octocat",
            pr=(
                PullRequest(
                    number=7,
                    title="Add widget",
                    url="https://github.com/acme/widget-src/pull/7",  # type: ignore[arg-type]
                )
                if with_pr
                else None
            ),
        ),
    )


def _context(**overrides: object) -> CommentContext:
    kwargs: dict[str, object] = {
        "payload": _payload(),
        "deploy_repo": "acme/deploy",
        "environment": "production",
        "environment_url": "https://widget.example.com",
        "update_mode": "pull_request",
        "bump_url": "https://github.com/acme/deploy/pull/42",
        "run_url": "https://github.com/acme/deploy/actions/runs/9",
        "state": CommentState.staged,
    }
    kwargs.update(overrides)
    return build_context(**kwargs)  # type: ignore[arg-type]


# --- placeholder vocabulary ---------------------------------------------------


def test_comment_template_keys_match_the_context_fields() -> None:
    """The validator's vocabulary is derived from the context, not restated.

    Mirrors the ``set(payload.value_format_kwargs()) == TEMPLATE_KEYS``
    invariant for ``set``/``environment_url`` templates: a placeholder added to
    or renamed on :class:`CommentContext` must be exactly what the validator
    accepts, with no chance of the two drifting.
    """
    context = _context()

    assert set(CommentContext.model_fields) == COMMENT_TEMPLATE_KEYS
    assert set(context.format_kwargs()) == COMMENT_TEMPLATE_KEYS


def test_format_kwargs_values_are_all_strings() -> None:
    """``str.format`` gets plain strings, so no template can hit a format-spec
    surprise from an int or a pydantic ``HttpUrl``."""
    for key, value in _context().format_kwargs().items():
        assert isinstance(value, str), key


# --- build_context ------------------------------------------------------------


def test_build_context_carries_payload_and_run_facts() -> None:
    context = _context()

    assert context.image_name == "ghcr.io/acme/widget"
    assert context.new_tag == "v1.2.3"
    assert context.digest == DIGEST
    assert context.git_sha == "c" * 40
    assert context.source_repo == "acme/widget-src"
    assert context.source_url == "https://github.com/acme/widget-src/pull/7"
    assert context.actor == "octocat"
    assert context.deploy_repo == "acme/deploy"
    assert context.environment == "production"
    assert context.update_mode == "pull_request"
    assert context.bump_url == "https://github.com/acme/deploy/pull/42"
    assert context.state == "staged"


def test_build_context_uses_release_tag_for_release_events() -> None:
    """``new_tag`` follows ``ClientPayload.new_tag()`` rather than ``tag``."""
    context = _context(payload=_payload(event="release", with_pr=False))

    assert context.new_tag == "main"


def test_build_context_falls_back_to_bump_url_for_a_missing_environment_url() -> None:
    """No template should ever render an empty markdown link target."""
    context = _context(environment_url=None)

    assert context.environment_url == "https://github.com/acme/deploy/pull/42"


# --- marker -------------------------------------------------------------------


def test_comment_marker_is_keyed_on_deploy_repo_image_and_environment() -> None:
    """One comment per (deploy repo, image, environment), so sibling bumps
    never overwrite each other's comment on the same source PR."""
    marker = comment_marker("acme/deploy", "ghcr.io/acme/widget", "production")

    assert marker == (
        "<!-- odp-releaser:comment key=acme/deploy|ghcr.io/acme/widget|production -->"
    )
    assert marker != comment_marker("acme/deploy", "ghcr.io/acme/widget", "staging")
    assert marker != comment_marker("acme/other", "ghcr.io/acme/widget", "production")
    assert marker != comment_marker("acme/deploy", "ghcr.io/acme/other", "production")


def test_render_comment_appends_the_marker_for_its_own_context() -> None:
    context = _context()

    body = render_comment("hello", context)

    assert body.startswith("hello")
    assert body.endswith(
        comment_marker(context.deploy_repo, context.image_name, context.environment)
    )


# --- rendering ----------------------------------------------------------------


def test_render_comment_substitutes_placeholders() -> None:
    body = render_comment("{image_name} -> {environment} ({state})", _context())

    assert "ghcr.io/acme/widget -> production (staged)" in body


@pytest.mark.parametrize(
    "template", [DEFAULT_STAGED_TEMPLATE, DEFAULT_DEPLOYED_TEMPLATE]
)
def test_builtin_templates_render_cleanly(template: str) -> None:
    body = render_comment(template, _context())

    assert "{" not in body.replace("{{", "").replace("}}", "")
    assert "ghcr.io/acme/widget" in body
    assert "v1.2.3" in body


def test_render_comment_honours_escaped_braces() -> None:
    """Literal braces in markdown (a Helm/Go template in a fence, `${{ }}`)
    must be written doubled, and come back single."""
    body = render_comment("`{{{{ .Values.image }}}}` for {image_name}", _context())

    assert "`{{ .Values.image }}` for ghcr.io/acme/widget" in body


def test_render_comment_raises_on_an_unknown_placeholder() -> None:
    """The validator predicts this ahead of time; the runtime must still not
    paper over it silently."""
    with pytest.raises(KeyError):
        render_comment("{not_a_placeholder}", _context())


def test_render_comment_of_an_empty_template_is_just_the_marker() -> None:
    """``staged: ""`` means "post nothing while staged"; callers check for an
    empty rendered body rather than a sentinel."""
    body = render_comment("", _context())

    assert body.strip() == comment_marker(
        "acme/deploy", "ghcr.io/acme/widget", "production"
    )


# --- synthesize_context -------------------------------------------------------


def test_synthesize_context_covers_every_placeholder() -> None:
    """Offline validation formats templates against this, so every key must be
    populated with something obviously synthetic."""
    context = synthesize_context()

    kwargs = context.format_kwargs()
    assert set(kwargs) == COMMENT_TEMPLATE_KEYS
    assert all(value for value in kwargs.values())
    assert render_comment(DEFAULT_DEPLOYED_TEMPLATE, context)
