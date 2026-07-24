"""Strict JSON Schema exports for the configs this project reads.

``ManifestConfig`` and ``DeployTarget`` deliberately keep pydantic's default
``extra="ignore"`` at runtime, so an editor's or CI's JSON Schema validator
gets no help catching a typo'd key on its own -- pydantic would just drop it
silently (see :mod:`odp_releaser.validation.unknown_keys`, which exists for
exactly that gap). The schemas generated here take the opposite stance:
every object schema gets ``additionalProperties: false`` bolted on, so a
generic JSON Schema validator (an editor extension, ``check-jsonschema``, ...)
rejects unknown keys the same way this project's own validator does, without
requiring odp-releaser itself to be installed.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from odp_releaser.schemas.dispatch import DeployTarget
from odp_releaser.schemas.manifest_config import ManifestConfig

_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_RAW_BASE = "https://raw.githubusercontent.com/gulfofmaine/odp-releaser/main/schemas"


def _make_strict(node: object) -> None:
    """Recursively add ``additionalProperties: false`` to every object schema.

    A schema object is treated as describing a fixed-shape model when it has
    a ``properties`` key -- that's true of the root schema and every entry
    under ``$defs``, but not of the free-form ``dict[str, ...]`` fields (e.g.
    ``images``, ``set``), which are typed instead via their own
    ``additionalProperties`` schema and must stay permissive.
    """
    if isinstance(node, dict):
        if "properties" in node:
            node["additionalProperties"] = False
        for value in node.values():
            _make_strict(value)
    elif isinstance(node, list):
        for item in node:
            _make_strict(item)


def _wrap(
    schema: dict[str, Any], *, schema_id: str, title: str, description: str
) -> dict[str, Any]:
    """Prepend ``$schema``/``$id``/``title``/``description`` to a generated schema.

    ``title`` and ``description`` from the generated schema (pydantic fills
    both in from the model's own name and docstring) are dropped in favor of
    the ones passed in, so the published schema reads well as a document,
    not just as a model dump.
    """
    wrapped: dict[str, Any] = {
        "$schema": _DRAFT,
        "$id": schema_id,
        "title": title,
        "description": description,
    }
    for key, value in schema.items():
        if key in ("title", "description"):
            continue
        wrapped[key] = value
    return wrapped


def image_manifest_schema() -> dict[str, Any]:
    """Build the strict JSON Schema for `image_manifest.yaml` configs.

    On top of the strictness every generated object schema gets, the root
    object also allows any ``x-``-prefixed key (via ``patternProperties``),
    matching this project's ``x-guards: &guards`` convention for sharing
    allowlists via YAML merge keys -- and the one prefix
    :func:`odp_releaser.validation.image_manifest.validate_image_manifest`
    tolerates only at the top level.
    """
    schema = ManifestConfig.model_json_schema()
    _make_strict(schema)
    schema["patternProperties"] = {"^x-": True}
    return _wrap(
        schema,
        schema_id=f"{_RAW_BASE}/image_manifest.schema.json",
        title="odp-releaser image manifest",
        description=(
            "Configuration read by `odp-releaser bump-images`, mapping "
            "image names to the manifests each should update on release."
        ),
    )


def deploy_targets_schema() -> dict[str, Any]:
    """Build the strict JSON Schema for `deploy_targets.yaml` configs.

    A deploy targets file is a top-level YAML array, so this schemas the
    array via :class:`pydantic.TypeAdapter` rather than a single model.
    """
    schema = TypeAdapter(list[DeployTarget]).json_schema()
    _make_strict(schema)
    return _wrap(
        schema,
        schema_id=f"{_RAW_BASE}/deploy_targets.schema.json",
        title="odp-releaser deploy targets",
        description=(
            "Configuration read by `odp-releaser notify`, listing the repos "
            "to send a `repository_dispatch` event to on release."
        ),
    )
