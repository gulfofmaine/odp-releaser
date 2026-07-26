"""Resolve 1-based source lines from a ruamel round-trip load.

Several validators (:mod:`odp_releaser.validation.unknown_keys`,
:mod:`odp_releaser.validation.image_manifest`,
:mod:`odp_releaser.validation.deploy_targets`) need a diagnostic's source
line, but only have one available when the document was parsed with
``ruamel.yaml.YAML()``'s default round-trip loader (not ``typ="safe"``),
which stashes line/column info on ``CommentedMap``/``CommentedSeq`` as
``.lc.data``. A plain ``dict``/``list`` -- or a value produced by a
non-round-trip loader -- simply has no ``lc`` attribute, so both lookups
below degrade to ``None`` instead of raising; every caller already treats a
missing line as "unknown", not an error, so a single shared implementation
keeps that fallback consistent instead of three subtly different copies.
"""

from __future__ import annotations

from collections.abc import Mapping


def line_for_key(data: object, key: object) -> int | None:
    """1-based source line of ``key`` in a round-trip-loaded mapping, if known."""
    if not isinstance(data, Mapping):
        return None
    line_col = getattr(data, "lc", None)
    lc_data = getattr(line_col, "data", None)
    if not isinstance(lc_data, dict) or key not in lc_data:
        return None
    key_line: int = lc_data[key][0]
    return key_line + 1


def line_for_index(data: object, index: int) -> int | None:
    """1-based source line of ``data[index]`` in a round-trip-loaded sequence."""
    if not isinstance(data, list):
        return None
    line_col = getattr(data, "lc", None)
    lc_data = getattr(line_col, "data", None)
    if not isinstance(lc_data, dict) or index not in lc_data:
        return None
    item_line: int = lc_data[index][0]
    return item_line + 1
