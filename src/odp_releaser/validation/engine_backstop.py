"""Run the real manifest engines as a validation backstop.

:mod:`odp_releaser.validation.image_manifest` hand-rolls checks that predict
what the manifest engines (:mod:`odp_releaser.manifests.kustomize`,
:mod:`odp_releaser.manifests.helm`, :mod:`odp_releaser.manifests.file`) will
do at bump time -- but those predictions can drift from the engines they
model. The engines are pure functions of ``(path, text, manifest, payload,
deployed_name, commit_message) -> str`` with no filesystem access of their own
(``bump_images._apply_manifest`` does all the reading and writing), which
makes them safe to call directly as an authoritative backstop: once a
manifest's hand-rolled checks report no error, :func:`run_engine_backstop`
runs the real engine against the manifest's actual text and turns any
exception it raises into a diagnostic. This is split out of
``image_manifest`` purely to keep that module under the project's
line-count budget; the two are one feature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import HttpUrl
from ruamel.yaml.error import YAMLError
from yamlpath.exceptions import YAMLPathException

from odp_releaser.manifests.helpers import ManifestLoadError
from odp_releaser.schemas.client_payload import ClientPayload, ClientPayloadSource

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from odp_releaser.schemas.manifest_config import ImageConfig
    from odp_releaser.validation.diagnostics import Diagnostics
    from odp_releaser.validation.location import ConfigLocation

# Obviously-synthetic placeholder values for :func:`synthesize_payload`. Not
# real hashes or hosts -- ``.invalid`` is the reserved (RFC 2606) TLD for
# addresses that are guaranteed never to resolve.
_SYNTHETIC_DIGEST = "sha256:" + "0" * 64
_SYNTHETIC_GIT_SHA = "0" * 40
_SYNTHETIC_REPO = "odp-releaser/synthetic-validation"
_SYNTHETIC_ACTOR = "odp-releaser-validator"
_SYNTHETIC_URL = "https://example.invalid/odp-releaser-synthetic-validation"


def representative_event(image_config: ImageConfig) -> str:
    """The event to synthesize an engine-backstop payload for, absent a real one.

    ``ClientPayload.new_tag()`` only branches on whether the event is
    ``"release"`` (using ``source.ref``) or anything else (using ``tag``), so
    that's the only distinction that matters here. A config that names its
    own ``events`` is exercised as its first listed event -- a release-only
    config is thus synthesized as a release; a config with no ``events``
    restriction (matches every event) defaults to ``"push"``, an arbitrary
    but representative non-release choice.
    """
    if image_config.events:
        return image_config.events[0]
    return "push"


def synthesize_payload(image_name: str, event: str) -> ClientPayload:
    """Build a ``ClientPayload`` for the engine backstop when no real one is available.

    The bare ``odp-releaser validate image-manifest`` entry point has no
    bump in flight and so no real payload to hand the engines -- but the
    engine backstop (see the module docstring) needs *some* ``ClientPayload``
    to call the real ``update_*_with_payload`` functions with. A synthetic
    one is sound here because those functions only ever read
    ``image_name``, ``tag``, ``digest``, ``git_sha`` and ``source.event`` off
    the payload (see ``ClientPayload.value_format_kwargs``/``new_tag``) --
    none of which affects *whether* a manifest applies cleanly, only *what*
    value ends up written into it. Every other field below is an
    obviously-synthetic placeholder (an ``.invalid`` URL, an all-zero
    digest/sha) rather than anything that could be mistaken for a real bump.

    Built fresh per ``event`` (see :func:`representative_event`) rather than
    shared across events, since ``new_tag()`` branches on ``source.event`` --
    reusing one payload regardless of event could silently skip exercising
    that branch for a config that only ever fires on release.

    A real ``payload`` -- passed in explicitly, or the one ``bump_images``'s
    own pre-flight already has in hand -- is always preferred over this
    synthetic one when available, since it reflects the actual bump about to
    happen rather than placeholder values.
    """
    return ClientPayload(
        image_name=image_name,
        digest=_SYNTHETIC_DIGEST,
        tag="synthetic",
        git_sha=_SYNTHETIC_GIT_SHA,
        image_ref=f"{image_name}@{_SYNTHETIC_DIGEST}",
        repo=_SYNTHETIC_REPO,
        source=ClientPayloadSource(
            event=event,
            ref="synthetic",
            url=HttpUrl(_SYNTHETIC_URL),
            run_url=HttpUrl(_SYNTHETIC_URL),
            actor=_SYNTHETIC_ACTOR,
        ),
    )


def run_engine_backstop[ManifestT](
    display_path: Path,
    text: str,
    manifest: ManifestT,
    update_fn: Callable[[Path, str, ManifestT, ClientPayload, str, list[str]], str],
    image_name: str,
    event: str,
    payload: ClientPayload | None,
    deployed_name: str,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Actually run the real engine against this manifest, as a backstop.

    Only called once a manifest's hand-rolled checks are done and reported no
    error for it (see the callers, which snapshot ``len(diagnostics.errors)``
    around their own checks) -- the engines are the authority on whether a
    bump would apply cleanly, so this exists to catch whatever failure mode
    the hand-rolled checks above don't model, not to duplicate what they
    already reported more precisely. ``update_fn`` returns a new string
    that's discarded here; nothing is read or written beyond the ``text``
    already in hand, so this is exactly as offline and read-only as every
    other check in :mod:`odp_releaser.validation.image_manifest` -- it just
    also happens to be exactly what ``bump_images`` itself would do with this
    manifest.

    A real ``payload`` is used when available (matching what ``bump_images``
    would actually apply); otherwise one is synthesized per
    :func:`synthesize_payload`. ``deployed_name`` must be the caller's own
    :func:`~odp_releaser.bump_images.effective_deployed_name` result -- this
    function has no ``ImageConfig`` to derive it from itself -- so the
    backstop calls each engine with exactly the name ``bump_images`` would.
    """
    try:
        effective_payload = (
            payload if payload is not None else synthesize_payload(image_name, event)
        )
        update_fn(display_path, text, manifest, effective_payload, deployed_name, [])
    except (
        YAMLPathException,
        KeyError,
        ValueError,
        ManifestLoadError,
        YAMLError,
    ) as exc:
        diagnostics.error(
            "bump-images would fail to apply this manifest at bump time "
            f"({type(exc).__name__}: {exc})",
            location=location.location,
            line=location.line,
        )
