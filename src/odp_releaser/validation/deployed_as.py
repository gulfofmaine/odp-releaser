"""Semantic checks tied to ``ImageConfig.deployed_as`` and the settings that key off it.

This is still one feature with ``image_manifest``: every function here is
called from its per-config or per-manifest walk and reuses the
``ConfigLocation``/``Diagnostics`` plumbing already threaded there rather
than duplicating it.

``ImageConfig.deployed_as`` only earns its keep if a config that declares it
wrong -- or a manifest that quietly disagrees with it -- gets caught before a
real bump. Every check below models one way that could go wrong at runtime:

- A ``deployed_as`` that isn't shaped like a name a real ``image.repository``
  selector or ``{deployed_image}`` substitution could ever equal (the same
  rule an ``images:`` key must satisfy -- see :mod:`.image_name_shape`).
- ``sync: true`` (directly or inherited from ``defaults``) with no
  ``deployed_as`` to copy the payload's image to.
- A ``deployed_as`` that just repeats the image's own ``images:`` key --
  legal (``effective_deployed_name`` falls back to that anyway), but a no-op
  declaration that suggests a copy-paste rather than a real mirror.
- A kustomize manifest whose ``images:`` entry's ``newName`` disagrees with,
  omits, or, the converse, carries a mirror that ``deployed_as`` never
  declares. This last case is exactly the drift preserved on purpose in
  ``tests/manifests/dagster_helm_kustomize/``: a commented-out ``newName``
  for an ECR mirror sitting next to a ``values.yaml`` that still carries the
  bare upstream name, with nothing noticing until this check existed.
- A ``file_manifests`` ``set`` value that hard-codes the upstream image name
  even though ``deployed_as`` says this manifest deploys from a mirror --
  almost certainly meant to be ``{deployed_image}``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from odp_releaser.schemas.manifest_config import resolve_setting
from odp_releaser.validation.image_name_shape import image_name_shape_problems

if TYPE_CHECKING:
    from collections.abc import Sequence

    from odp_releaser.schemas.manifest_config import ConfigDefaults, ImageConfig
    from odp_releaser.validation.diagnostics import Diagnostics
    from odp_releaser.validation.location import ConfigLocation


def check_deployed_as_and_sync(
    image_name: str,
    image_config: ImageConfig,
    defaults: ConfigDefaults,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """The checks that only need this one config's own declared settings.

    ``sync`` is resolved via
    :func:`~odp_releaser.schemas.manifest_config.resolve_setting` against
    ``defaults.sync`` before judging it, exactly like ``bump_images`` itself
    would resolve it -- a bare ``image_config.sync`` would miss a repo-wide
    ``defaults: sync: true`` that applies here just as much as an explicit
    per-config ``sync: true`` would, and this config would still have
    nothing to sync to.
    """
    deployed_as = image_config.deployed_as
    if deployed_as is not None:
        problems = image_name_shape_problems(deployed_as)
        if problems:
            message = (
                f"deployed_as {deployed_as!r} {'; '.join(problems)}: it is "
                "used as the Helm dagster shorthand's image.repository "
                "selector and in `set` templating via {deployed_image}, so "
                "it must be a plain image name like the payload's own "
                "image_name"
            )
            diagnostics.error(
                message,
                location=location.child("deployed_as").location,
                line=location.line,
            )
        if deployed_as == image_name:
            diagnostics.warning(
                f"deployed_as is set to {deployed_as!r}, the same as this "
                "image's own images key; effective_deployed_name already "
                "falls back to that when deployed_as is unset, so this "
                "declares nothing new",
                location=location.child("deployed_as").location,
                line=location.line,
            )

    sync = resolve_setting(image_config.sync, defaults.sync)
    if sync and deployed_as is None:
        diagnostics.error(
            "sync is true but deployed_as is unset; there is nothing to "
            "copy the payload's image to",
            location=location.child("sync").location,
            line=location.line,
        )


def check_deployed_as_collisions(
    image_configs_by_name: Mapping[str, Sequence[ImageConfig]],
    defaults: ConfigDefaults,
    diagnostics: Diagnostics,
) -> None:
    """Flag two different ``images:`` keys resolving the same ``deployed_as``.

    Grouped by ``deployed_as`` value and then by the *owning* image key, so
    one image declaring the same ``deployed_as`` across several of its own
    configs never counts (legitimate -- a ``sync`` fans out per config, and
    ``check_cross_config`` already covers a shared manifest file within one
    image); only two or more distinct image keys sharing it do. ``sync`` is
    resolved the same way :func:`check_deployed_as_and_sync` does, since a
    repo-wide ``defaults: sync: true`` is exactly as dangerous here as an
    explicit per-config one. Error when at least two of the colliding images
    resolve ``sync`` true: they would race to copy different upstream images
    onto the same mirror tag, last write wins. Warning otherwise: nothing is
    overwritten, but the images still can't both legitimately deploy from
    one name.

    Callers building ``image_configs_by_name`` from a ``payload``-filtered
    run naturally leave only one image key in scope, so this can never fire
    there -- correctly, since a cross-image collision can't be observed
    looking at one image at a time.
    """
    images_by_deployed_as: dict[str, dict[str, bool]] = {}
    for image_name, image_configs in image_configs_by_name.items():
        for image_config in image_configs:
            deployed_as = image_config.deployed_as
            if deployed_as is None:
                continue
            sync = bool(resolve_setting(image_config.sync, defaults.sync))
            images = images_by_deployed_as.setdefault(deployed_as, {})
            images[image_name] = images.get(image_name, False) or sync

    for deployed_as, images in images_by_deployed_as.items():
        if len(images) < 2:
            continue
        names = sorted(images)
        syncing = [name for name in names if images[name]]
        if len(syncing) >= 2:
            diagnostics.error(
                f"deployed_as {deployed_as!r} is declared with sync: true by "
                f"more than one image ({', '.join(syncing)}); they would "
                "copy different upstream images to the same mirror tag, and "
                "whichever bump runs last wins"
            )
        else:
            diagnostics.warning(
                f"deployed_as {deployed_as!r} is declared by more than one "
                f"image ({', '.join(names)}); each would deploy from this "
                "mirror expecting a different upstream image"
            )


def check_kustomize_deployed_as_agreement(
    deployed_as: str | None,
    images_node: Any,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """A kustomize ``images:`` entry's ``newName`` must agree with ``deployed_as``.

    ``images_node`` is whatever
    :func:`~odp_releaser.validation.image_manifest._get_node` already
    resolved for this manifest's own ``/images[name=...]`` entry -- taken as
    a parameter, rather than re-resolved here, so this never re-walks a
    processor its caller (``_validate_kustomize``) already walked. A no-op
    when that entry doesn't exist at all (already reported by the pin
    checks) or the manifest never loaded, since ``images_node`` is anything
    but a mapping either way.

    Kustomize's ``newName`` is the only place in a kustomize manifest that
    actually names a mirror registry (see ``update_kustomize_with_payload``'s
    docstring for why the ``images:`` entry itself always stays keyed on the
    upstream name); ``deployed_as`` is the config's own claim about that same
    fact. The two describing different things -- a config declaring a mirror
    the manifest doesn't implement, a manifest naming a mirror nothing
    declares, or the two simply naming different mirrors -- is exactly the
    kind of drift that stays invisible until ``sync`` or the Helm dagster
    shorthand silently use the wrong name.
    """
    if not isinstance(images_node, Mapping):
        return
    new_name = images_node.get("newName")
    if deployed_as is not None:
        if new_name is None:
            diagnostics.error(
                f"deployed_as is {deployed_as!r}, but this images: entry has "
                "no newName; kustomize would still render the upstream "
                "image, not the mirror this config declares",
                location=location.location,
                line=location.line,
            )
        elif new_name != deployed_as:
            diagnostics.error(
                f"this images: entry's newName is {new_name!r}, but "
                f"deployed_as is {deployed_as!r}; the manifest and the "
                "config disagree about which registry this image actually "
                "deploys from",
                location=location.location,
                line=location.line,
            )
    elif new_name is not None:
        diagnostics.warning(
            f"this images: entry sets newName to {new_name!r}, but no "
            "deployed_as declares it, so sync and the Helm dagster "
            "shorthand can't see this mirror",
            location=location.location,
            line=location.line,
        )


def check_file_manifest_set_upstream_name(
    set_paths: dict[str, str],
    deployed_as: str | None,
    upstream_name: str,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Warn on a ``file_manifests`` ``set`` value hard-coding the upstream image name.

    Only checked when ``deployed_as`` is set: this manifest deploys from the
    mirror, so a ``set`` value that still spells out ``upstream_name``
    literally is about to write the wrong registry into it -- almost
    certainly meant to be ``{deployed_image}`` instead, which
    ``apply_set_templates`` would have formatted to the mirror name (see its
    docstring). ``upstream_name`` is the payload's ``image_name`` when a real
    payload is available, else the config's own ``images:`` key -- the same
    fallback :func:`~odp_releaser.validation.image_manifest._validate_config_item`
    uses to predict ``deployed_name`` without a payload.
    """
    if deployed_as is None:
        return
    for selector, value in set_paths.items():
        # `deployed_as not in value` is what keeps this off the primary
        # documented shape. A pull-through path contains the upstream name by
        # construction (`<account>.dkr.ecr…/docker-hub/gmri/app` ends in
        # `gmri/app`), so a value that correctly hard-codes the *mirror* also
        # matches the substring test and would be reported as hard-coding the
        # upstream name. Suggesting `{deployed_image}` there is still
        # reasonable advice, but the stated diagnosis would be false.
        if upstream_name in value and deployed_as not in value:
            diagnostics.warning(
                f"{value!r} hard-codes the upstream image name "
                f"{upstream_name!r}, but deployed_as={deployed_as!r} means "
                "this manifest deploys from the mirror; use "
                "{deployed_image} instead",
                location=location.child(f'"{selector}"').location,
                line=location.line,
            )
