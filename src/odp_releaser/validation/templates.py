"""Validate ``set``/``environment_url``/comment template values and yamlpath selectors.

Split out of :mod:`odp_releaser.validation.image_manifest` purely to keep
that module under the project's line-count budget -- the same reason
:mod:`.cross_config` and :mod:`.engine_backstop` were split out earlier (see
those modules' docstrings); the two are one feature. This is one coherent
concern in its own right, though: every failure mode of the
``value.format(**kwargs)`` call ``apply_set_templates`` makes at bump time,
and every failure mode of the yamlpath selector resolution ``bump_images``
performs against a manifest's ``set`` paths, live here together -- one is
about what a templated string may contain, the other about where it may
point, but both are checked ahead of the same bump-time call sites so a typo
in either shows up as a fast, readable diagnostic instead of a mid-release
``KeyError``/``YAMLPathException``.
"""

from __future__ import annotations

from string import Formatter
from typing import TYPE_CHECKING

from yamlpath import YAMLPath
from yamlpath.exceptions import YAMLPathException

from odp_releaser.comment_body import COMMENT_TEMPLATE_KEYS, synthesize_context

if TYPE_CHECKING:
    from yamlpath import Processor

    from odp_releaser.schemas.client_payload import ClientPayload
    from odp_releaser.schemas.manifest_config import CommentConfig
    from odp_releaser.validation.diagnostics import Diagnostics
    from odp_releaser.validation.location import ConfigLocation

# The placeholders every ``set``/``environment_url`` template may reference.
# Four of these are drawn straight from :meth:`ClientPayload.value_format_kwargs`;
# ``deployed_image`` is not one of them -- ``apply_set_templates`` adds it
# itself (see its docstring), formatted from ``effective_deployed_name``
# rather than read off the payload -- so it's listed here alongside the
# payload-derived keys but excluded from the cross-check below. Kept here
# (rather than re-derived) and cross-checked by a test against a real
# payload's ``value_format_kwargs()`` keys plus ``deployed_image``, so the
# two can't silently drift if a placeholder is ever added or renamed on
# either side.
TEMPLATE_KEYS: frozenset[str] = frozenset(
    {"new_tag", "git_sha", "digest", "payload", "deployed_image"}
)


def validate_set(
    set_paths: dict[str, str],
    location: ConfigLocation,
    diagnostics: Diagnostics,
    processor: Processor | None,
    payload: ClientPayload | None,
    deployed_name: str,
) -> None:
    """Check every ``set`` selector/value pair for one manifest.

    ``deployed_name`` is folded into the real-``.format()`` check the same
    way ``apply_set_templates`` folds it in at bump time -- as
    ``{"deployed_image": deployed_name}`` alongside ``payload``'s own
    keyword arguments -- so a ``set`` value that legitimately uses
    ``{deployed_image}`` isn't reported as failing to format merely because
    this check built a narrower kwargs dict than the real engine will.
    """
    format_kwargs = (
        payload.value_format_kwargs() | {"deployed_image": deployed_name}
        if payload is not None
        else None
    )
    for selector, value in set_paths.items():
        selector_location = location.child(f'"{selector}"')
        validate_selector(selector, selector_location, diagnostics, processor)
        validate_template_value(
            value,
            selector_location,
            diagnostics,
            payload,
            format_kwargs=format_kwargs,
        )


def validate_selector(
    selector: str,
    location: ConfigLocation,
    diagnostics: Diagnostics,
    processor: Processor | None,
) -> None:
    """A ``set`` selector must parse, and (if the file loaded) must resolve.

    ``YAMLPath`` parses lazily -- constructing it never raises, only
    accessing ``.escaped`` does -- so parseability has to be forced rather
    than just constructed and discarded.
    """
    try:
        path = YAMLPath(selector)
        _ = path.escaped
    except YAMLPathException as exc:
        diagnostics.error(
            f"{selector!r} is not a valid yamlpath: {exc}",
            location=location.location,
            line=location.line,
        )
        return

    if processor is None:
        return
    try:
        list(processor.get_nodes(selector, mustexist=True))
    except YAMLPathException as exc:
        diagnostics.error(
            f"{selector!r} does not resolve in its target manifest: {exc}",
            location=location.location,
            line=location.line,
        )


def validate_comment_templates(
    comment: CommentConfig | None,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Both comment templates only use known, ``.format``-safe placeholders.

    Comment templates have their own vocabulary (see
    :mod:`odp_releaser.comment_body`) and are checked against a synthetic
    context rather than the payload, since most of what a comment can reference
    -- the deploy repo, the bump URL, the resolved environment -- isn't known
    until the bump runs. ``warn_if_no_placeholder`` is off because a wholly
    static comment is a legitimate thing to want.
    """
    if comment is None:
        return
    context_kwargs = synthesize_context().format_kwargs()
    for field in ("staged", "deployed"):
        template: str | None = getattr(comment, field)
        if not template:
            continue
        validate_template_value(
            template,
            location.child(f"comment.{field}"),
            diagnostics,
            None,
            warn_if_no_placeholder=False,
            valid_keys=COMMENT_TEMPLATE_KEYS,
            format_kwargs=context_kwargs,
        )


def validate_template_value(
    value: str,
    location: ConfigLocation,
    diagnostics: Diagnostics,
    payload: ClientPayload | None,
    *,
    warn_if_no_placeholder: bool = True,
    valid_keys: frozenset[str] = TEMPLATE_KEYS,
    format_kwargs: dict[str, str] | None = None,
) -> None:
    """A templated value only uses known, ``.format``-safe placeholders.

    ``apply_set_templates`` calls ``value.format(**payload.value_format_kwargs())``
    at bump time, so every failure mode of that call is checked here ahead of
    time: an unknown field name (``KeyError``), a positional field (the
    runtime only ever passes keyword arguments, so ``{0}``/``{}`` also raise
    ``KeyError``), attribute/index access (``{payload.foo}`` -- technically
    ``str.format`` would attempt it, but nothing in ``value_format_kwargs()``
    supports it usefully, so it's flagged rather than silently misbehaving),
    and a stray ``{``/``}`` (``ValueError`` from ``Formatter().parse``
    itself). When real format arguments are available and none of those static
    checks already flagged the value, the actual ``value.format(**kwargs)``
    call is attempted too, so the check is faithful to a real ``bump-images``
    run rather than only to the placeholder names -- but a value already
    reported above isn't reported a second time for failing the very call that
    was predicted to fail.

    ``valid_keys``/``format_kwargs`` default to the ``set``/
    ``environment_url`` vocabulary (:data:`TEMPLATE_KEYS` and the payload's
    own ``value_format_kwargs()``). Comment templates pass their own pair --
    :data:`~odp_releaser.comment_body.COMMENT_TEMPLATE_KEYS` and a synthetic
    context -- because a comment may reference deploy-side run facts that have
    no business being templated into a manifest.
    """
    reported = False
    try:
        parsed = list(Formatter().parse(value))
    except ValueError as exc:
        diagnostics.error(
            f"{value!r} is not a valid format string: {exc}",
            location=location.location,
            line=location.line,
        )
        return

    field_names = [
        field_name for _, field_name, _, _ in parsed if field_name is not None
    ]

    for field_name in field_names:
        if field_name == "" or field_name.isdigit():
            diagnostics.error(
                f"{value!r} uses a positional placeholder "
                f"'{{{field_name}}}'; bump-images calls "
                "str.format(**kwargs), so only named placeholders work",
                location=location.location,
                line=location.line,
            )
        elif "." in field_name or "[" in field_name:
            diagnostics.error(
                f"{value!r} uses attribute/index access "
                f"'{{{field_name}}}', which is not supported; only bare "
                "placeholder names are",
                location=location.location,
                line=location.line,
            )
        elif field_name not in valid_keys:
            known = ", ".join(sorted(valid_keys))
            diagnostics.error(
                f"{value!r} references unknown placeholder "
                f"'{{{field_name}}}'; valid placeholders are: {known}",
                location=location.location,
                line=location.line,
            )
        else:
            continue
        reported = True

    if warn_if_no_placeholder and not field_names:
        diagnostics.warning(
            f"{value!r} has no template placeholder, so bump-images can "
            "never change it",
            location=location.location,
            line=location.line,
        )

    if format_kwargs is None and payload is not None:
        format_kwargs = payload.value_format_kwargs()
    if format_kwargs is not None and not reported:
        try:
            value.format(**format_kwargs)
        except (KeyError, ValueError, IndexError) as exc:
            diagnostics.error(
                f"{value!r} failed to format with the real payload: {exc}",
                location=location.location,
                line=location.line,
            )
