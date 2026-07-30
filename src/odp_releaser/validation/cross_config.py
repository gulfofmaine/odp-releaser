"""Checks that only make sense with every config matching an event visible at once.

:mod:`odp_releaser.validation.image_manifest` checks each image config on its
own, but a duplicate manifest target, an unused reviewer list, or a
disagreeing setting isn't a property of any one config in isolation -- it's a
collision or disagreement *between* sibling configs that ``bump_images``
groups together by matching event before resolving settings or applying
manifests. The checks here group the same way, so they can see what
``bump_images`` itself sees.
"""

from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, get_args

from odp_releaser.manifests.helpers import resolve_manifest_path
from odp_releaser.schemas.manifest_config import (
    ImageConfig,
    config_matches_event,
    resolve_comment_config,
    resolve_setting,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from odp_releaser.schemas.manifest_config import (
        ConfigDefaults,
        FileManifest,
        HelmManifest,
        KustomizeManifest,
    )
    from odp_releaser.validation.diagnostics import Diagnostics
    from odp_releaser.validation.location import ConfigLocation

# The settings ``bump_images`` resolves per matching config (falling back to
# `defaults` via `resolve_setting`) and warns about when matching configs for
# one event disagree; kept as a tuple (not hardcoded per-call) so the
# agreement check covers exactly the same set `bump_images` itself resolves.
_AGREEMENT_ATTRS = (
    "environment",
    "environment_url",
    "reviewers",
    "team_reviewers",
    "comment",
)

# Events whose client payload can carry a source pull request. `make_payload`
# only looks one up for `push` (see `resolve_client_payload`), so a config that
# fires on nothing else can never have a pull request to comment on.
_EVENTS_WITH_PULL_REQUESTS = frozenset({"push"})


def _known_events() -> tuple[str, ...]:
    """The event literals ``ImageConfig.events`` accepts, read off the schema.

    Derived via ``get_args`` (rather than hardcoded) so this can't drift from
    :class:`~odp_releaser.schemas.manifest_config.ImageConfig` if an event is
    ever added or renamed there.
    """
    # pylint doesn't model pydantic's model_fields as a mapping.
    annotation = ImageConfig.model_fields["events"].annotation  # pylint: disable=unsubscriptable-object
    list_type = next(arg for arg in get_args(annotation) if arg is not NoneType)
    (literal_type,) = get_args(list_type)
    return get_args(literal_type)


def check_cross_config(
    config_path: Path,
    image_name: str,
    image_configs: Sequence[ImageConfig],
    defaults: ConfigDefaults,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Checks that only make sense looking at a whole event's configs together.

    ``bump_images`` groups configs by which ones match a given event (a
    config with ``events: None`` matches every event) before resolving
    settings or applying manifests, so these checks group the same way.
    Distinct events can produce the exact same matching group (e.g. two
    configs that both leave ``events`` unset match every event identically);
    ``seen_groups`` collapses those so the same disagreement isn't reported
    once per event.
    """
    seen_groups: set[frozenset[int]] = set()
    for event in _known_events():
        indices = frozenset(
            index
            for index, image_config in enumerate(image_configs)
            if config_matches_event(image_config, event)
        )
        if not indices or indices in seen_groups:
            continue
        seen_groups.add(indices)
        matching = [image_configs[index] for index in sorted(indices)]

        _check_duplicate_manifest_targets(
            config_path, image_name, event, matching, location, diagnostics
        )
        _check_unused_reviewers(
            image_name, event, matching, defaults, location, diagnostics
        )
        _check_unusable_comment(
            image_name, event, matching, defaults, location, diagnostics
        )
        if len(matching) > 1:
            _check_setting_agreement(
                image_name, event, matching, defaults, location, diagnostics
            )


def _check_duplicate_manifest_targets(
    config_path: Path,
    image_name: str,
    event: str,
    matching: Sequence[ImageConfig],
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Warn on duplicate manifest targets, and on those targets' configs disagreeing on ``deployed_as``.

    Both checks walk the exact same per-config, per-manifest grouping (by
    resolved path), so they're computed together in one pass rather than
    walking the same manifests twice:

    - The same resolved path targeted more than once for one event is
      redundant -- ``bump-images`` applies every one of them.
    - The same resolved path targeted by configs that resolve *different*
      ``deployed_as`` values is worse than redundant: unlike
      :data:`_AGREEMENT_ATTRS`, differing ``deployed_as`` across configs is
      normal in general (it's resolved per config, never across them --
      see ``effective_deployed_name``'s docstring) -- but a single manifest
      file (a kustomize ``newName``, a Helm ``image.repository``) can only
      agree with one mirror name at a time, so two configs writing the same
      file while claiming different mirrors means at least one of them is
      wrong about what that manifest actually deploys.
    """
    counts: dict[Path, int] = {}
    deployed_as_by_path: dict[Path, set[str]] = {}
    for image_config in matching:
        manifests: list[KustomizeManifest | HelmManifest | FileManifest] = [
            *image_config.kustomize_manifests,
            *image_config.helm_charts,
            *image_config.file_manifests,
        ]
        deployed_as = image_config.deployed_as or image_name
        for manifest in manifests:
            resolved = resolve_manifest_path(config_path, manifest.path)
            counts[resolved] = counts.get(resolved, 0) + 1
            deployed_as_by_path.setdefault(resolved, set()).add(deployed_as)

    for resolved, count in counts.items():
        if count > 1:
            diagnostics.warning(
                f"{resolved} is targeted {count} times by configs "
                f"matching event {event!r} for image {image_name!r}; "
                "bump-images will apply all of them, redundantly",
                location=location.location,
                line=location.line,
            )

    for resolved, deployed_names in deployed_as_by_path.items():
        if len(deployed_names) > 1:
            diagnostics.warning(
                f"{resolved} is targeted by configs matching event {event!r} "
                f"for image {image_name!r} that resolve different "
                f"deployed_as values ({sorted(deployed_names)}); the "
                "manifest can only agree with one of them",
                location=location.location,
                line=location.line,
            )


def _effective_update_mode(matching: Sequence[ImageConfig]) -> str:
    """The ``update_mode`` matching configs would resolve to at bump time.

    Mirrors ``bump_images.bump_images`` (~186-193): it collects
    ``update_mode`` across every authorized config matching the event and
    unconditionally prefers ``"pull_request"`` if *any* of them sets it,
    regardless of config order -- unlike the ``resolve_setting``-based
    attributes below, where the first config's value wins. Check this
    against that runtime block if the two ever seem to disagree.
    """
    if any(image_config.update_mode == "pull_request" for image_config in matching):
        return "pull_request"
    return "commit"


def _effective_setting[SettingT](
    matching: Sequence[ImageConfig], default: SettingT | None, attr: str
) -> SettingT | None:
    """The value ``bump_images._resolve_config_setting`` would resolve ``attr`` to.

    Mirrors that function (``bump_images.py`` ~336-363) exactly, minus its
    logging: each matching config's own value falls back to ``default`` via
    ``resolve_setting``, and the first config (in order) whose resolved
    value is not ``None`` wins. Returns ``default`` itself when no matching
    config resolves to a value at all.
    """
    values: list[SettingT] = [
        resolved
        for image_config in matching
        if (resolved := resolve_setting(getattr(image_config, attr), default))
        is not None
    ]
    return values[0] if values else default


def _check_unused_reviewers(
    image_name: str,
    event: str,
    matching: Sequence[ImageConfig],
    defaults: ConfigDefaults,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Warn when the group's *effective* settings mean reviewers go unused.

    A single config's own ``update_mode`` isn't enough to know whether its
    ``reviewers``/``team_reviewers`` are ever requested: ``bump_images``
    resolves ``update_mode`` across every config matching an event before
    applying any of them (see :func:`_effective_update_mode`), so a sibling
    config that sets ``update_mode: pull_request`` for the same event forces
    pull-request mode for this config too, even though this config alone
    says ``commit``. This mirrors that same cross-config resolution -- both
    for ``update_mode`` and for ``reviewers``/``team_reviewers`` (each via
    :func:`_effective_setting`, mirroring
    ``bump_images._resolve_config_setting``) -- so it only warns when
    reviewers are configured but truly can never be requested for this
    event, naming the event since a config can be ``commit`` for one event
    and ``pull_request`` for another.
    """
    if _effective_update_mode(matching) != "commit":
        return
    reviewers = _effective_setting(matching, defaults.reviewers, "reviewers") or []
    team_reviewers = (
        _effective_setting(matching, defaults.team_reviewers, "team_reviewers") or []
    )
    if reviewers or team_reviewers:
        diagnostics.warning(
            f"reviewers/team_reviewers are set but update_mode resolves to "
            f"'commit' for event {event!r} on image {image_name!r}; only "
            "pull_request mode ever requests reviewers, so these are never "
            "used",
            location=location.location,
            line=location.line,
        )


def _check_unusable_comment(
    image_name: str,
    event: str,
    matching: Sequence[ImageConfig],
    defaults: ConfigDefaults,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Warn when a comment written on *this config* can never be posted.

    Both checks look only at ``comment`` set on the image configs themselves,
    never at what they inherit. Two levels of inheritance would each produce
    noise if followed:

    - Commenting is on by default with built-in templates, so judging the
      resolved value would fire on every release-only or commit-mode config in
      every existing manifest -- describing the shipped default, not a mistake.
    - A ``defaults:``-level ``comment`` is by design a broad brush across images
      with different events and update modes. Some of those images firing on
      ``release`` is normal, and flagging each one for not using a repo-wide
      default would punish a perfectly good default.

    A config that spells out ``comment:`` itself, though, is making a claim
    about *that* config, and these two situations silently defeat it:

    - The event carries no source pull request. ``make_payload`` only resolves
      one for ``push`` events, so a release-only or workflow_dispatch-only
      config has nothing to comment on no matter what its templates say.
    - Its ``staged`` template can never be reached, because ``update_mode``
      resolves to ``commit`` for this event and a direct commit is reported as
      deployed immediately. This mirrors :func:`_check_unused_reviewers`,
      including its reason for resolving ``update_mode`` across the whole
      matching group rather than per config.
    """
    configured = [
        image_config.comment
        for image_config in matching
        if image_config.comment is not None
    ]
    if not configured:
        return

    comment = resolve_comment_config(
        _effective_setting(matching, defaults.comment, "comment"), defaults.comment
    )
    if not comment.enabled:
        return

    if event not in _EVENTS_WITH_PULL_REQUESTS:
        diagnostics.warning(
            f"a comment is configured for image {image_name!r} but event "
            f"{event!r} never carries a source pull request (only push events "
            "do), so no comment can be posted for it",
            location=location.location,
            line=location.line,
        )
        return

    explicit_staged = any(setting.staged for setting in configured)
    if explicit_staged and _effective_update_mode(matching) == "commit":
        diagnostics.warning(
            f"comment.staged is set but update_mode resolves to 'commit' for "
            f"event {event!r} on image {image_name!r}; a direct commit is "
            "reported as deployed immediately, so only comment.deployed is "
            "ever used",
            location=location.location,
            line=location.line,
        )


def _check_setting_agreement(
    image_name: str,
    event: str,
    matching: Sequence[ImageConfig],
    defaults: ConfigDefaults,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Warn when matching configs disagree on a setting ``bump_images`` resolves once.

    ``update_mode`` and the ``resolve_setting``-ed attributes are resolved
    differently at runtime (see :func:`_effective_update_mode` and
    :func:`_effective_setting`), so their disagreement messages must say
    different things: ``update_mode`` always ends up ``pull_request`` if any
    matching config sets it, while the other attributes keep the first
    config's value.
    """
    update_modes = {image_config.update_mode for image_config in matching}
    if len(update_modes) > 1:
        diagnostics.warning(
            f"configs matching event {event!r} for image "
            f"{image_name!r} disagree on update_mode ({sorted(update_modes)}); "
            "bump-images ignores config order for update_mode and uses "
            "pull_request since at least one matching config sets it",
            location=location.location,
            line=location.line,
        )

    for attr in _AGREEMENT_ATTRS:
        values = [
            resolve_setting(getattr(image_config, attr), getattr(defaults, attr))
            for image_config in matching
        ]
        present = [value for value in values if value is not None]
        distinct = [
            value for i, value in enumerate(present) if value not in present[:i]
        ]
        if len(distinct) > 1:
            diagnostics.warning(
                f"configs matching event {event!r} for image "
                f"{image_name!r} disagree on {attr} ({distinct}); "
                "bump-images warns and uses the first",
                location=location.location,
                line=location.line,
            )
