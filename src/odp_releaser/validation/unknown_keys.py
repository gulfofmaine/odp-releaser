"""Report YAML mapping keys that no pydantic field accepts.

The config models (:mod:`odp_releaser.schemas.manifest_config`) use pydantic's
default ``extra="ignore"``, so a typo like ``kustomize_manifest:`` (missing
the ``s``) is silently dropped rather than raising: the field never updates
and nothing tells the author why. This walker re-checks a raw parsed mapping
against a model's declared fields and reports whatever pydantic itself
discarded, recursing into nested models so a typo anywhere in the config tree
is caught, not just at the top level.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from typing import TYPE_CHECKING, Union, get_args, get_origin

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic.fields import FieldInfo

    from odp_releaser.validation.diagnostics import Diagnostics

# ``X | None`` (PEP 604) and ``typing.Optional[X]``/``typing.Union[X, None]``
# resolve to different origins depending on which syntax a field's annotation
# used; both are treated as unions here so either spelling recurses the same way.
_UNION_ORIGINS = (types.UnionType, Union)


def report_unknown_keys(
    model_cls: type[BaseModel],
    data: object,
    diagnostics: Diagnostics,
    *,
    location: str = "",
    allowed_key_prefixes: Sequence[str] = (),
) -> None:
    """Recursively report keys in ``data`` that ``model_cls`` doesn't declare.

    ``data`` is the raw value parsed from YAML (ideally via a ruamel
    round-trip loader, so line numbers are available), not a validated model
    instance — validating first would already have silently dropped the keys
    this exists to catch.

    A non-mapping ``data`` (e.g. the bare-string shorthand accepted by
    ``KustomizeManifest``'s ``coerce_path_string`` validator) has no keys to
    check and returns immediately.

    ``allowed_key_prefixes`` only excuses keys at this call's own level —
    e.g. the caller passes ``("x-",)`` at the top of an image manifest so
    ``x-guards: &guards`` (this project's convention for YAML anchors shared
    via merge keys) isn't flagged. It is intentionally not threaded into the
    recursive calls below: an anchor convention adopted at one level of the
    config doesn't imply every nested mapping should tolerate arbitrary
    ``x-`` keys too.
    """
    if not isinstance(data, Mapping):
        return

    fields = model_cls.model_fields
    for key in data:
        if isinstance(key, str) and any(
            key.startswith(prefix) for prefix in allowed_key_prefixes
        ):
            continue
        if key not in fields:
            diagnostics.error(
                _unknown_key_message(key, fields),
                line=_line_for_key(data, key),
                location=location or None,
            )

    for name, field in fields.items():
        if name not in data:
            continue
        field_location = f"{location}.{name}" if location else name
        _recurse_into_field(field.annotation, data[name], diagnostics, field_location)


def _unknown_key_message(key: object, fields: Mapping[str, FieldInfo]) -> str:
    valid_keys = ", ".join(sorted(fields))
    if not valid_keys:
        return f"Unknown key {key!r}"
    return f"Unknown key {key!r}; valid keys are: {valid_keys}"


def _line_for_key(data: Mapping[object, object], key: object) -> int | None:
    """Resolve a mapping key's 1-based source line from ruamel round-trip line info.

    A round-trip-loaded ``CommentedMap`` exposes ``data.lc.data[key]`` as a
    ``(key_line, key_col, value_line, value_col)`` tuple, 0-based. A plain
    ``dict`` (or a map loaded by a non-round-trip loader) has no ``lc``
    attribute at all, so the lookup is guarded with ``getattr``/membership
    checks rather than assumed to be present, and yields ``None`` instead of
    raising.
    """
    line_col = getattr(data, "lc", None)
    if line_col is None:
        return None
    lc_data = getattr(line_col, "data", None)
    if not isinstance(lc_data, dict) or key not in lc_data:
        return None
    key_line: int = lc_data[key][0]
    return key_line + 1


def _recurse_into_field(
    annotation: object,
    value: object,
    diagnostics: Diagnostics,
    location: str,
) -> None:
    """Dispatch on a field's annotation shape and recurse into any nested models it holds."""
    if annotation is None:
        return

    origin = get_origin(annotation)

    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            report_unknown_keys(annotation, value, diagnostics, location=location)
        return

    if origin in _UNION_ORIGINS:
        for member in get_args(annotation):
            if isinstance(member, type) and issubclass(member, BaseModel):
                report_unknown_keys(member, value, diagnostics, location=location)
        return

    if origin is list:
        _recurse_into_list_field(annotation, value, diagnostics, location)
        return

    if origin is dict:
        _recurse_into_dict_field(annotation, value, diagnostics, location)


def _recurse_into_list_field(
    annotation: object,
    value: object,
    diagnostics: Diagnostics,
    location: str,
) -> None:
    (item_type,) = get_args(annotation)
    if not (isinstance(item_type, type) and issubclass(item_type, BaseModel)):
        return
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        report_unknown_keys(
            item_type, item, diagnostics, location=f"{location}[{index}]"
        )


def _recurse_into_dict_field(
    annotation: object,
    value: object,
    diagnostics: Diagnostics,
    location: str,
) -> None:
    """Handle ``dict[str, Model]`` and ``dict[str, list[Model]]`` fields (e.g. ``images``)."""
    _key_type, value_type = get_args(annotation)
    if not isinstance(value, Mapping):
        return

    if isinstance(value_type, type) and issubclass(value_type, BaseModel):
        for key, item in value.items():
            report_unknown_keys(
                value_type, item, diagnostics, location=f'{location}."{key}"'
            )
        return

    if get_origin(value_type) is list:
        (item_type,) = get_args(value_type)
        if not (isinstance(item_type, type) and issubclass(item_type, BaseModel)):
            return
        for key, items in value.items():
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                report_unknown_keys(
                    item_type,
                    item,
                    diagnostics,
                    location=f'{location}."{key}"[{index}]',
                )
