"""Load and cache manifest file text on behalf of the image-manifest validator.

Split out of :mod:`odp_releaser.validation.image_manifest` purely to keep
that module under the project's line-count budget; conceptually this is
part of the same feature (see that module's docstring for the full
rationale on why text, not a parsed ``Processor``, is what gets cached).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ruamel.yaml.error import YAMLError

from odp_releaser.manifests.helpers import (
    ManifestLoadError,
    open_for_editing,
    resolve_manifest_path,
)

if TYPE_CHECKING:
    from pathlib import Path

    from odp_releaser.validation.diagnostics import Diagnostics
    from odp_releaser.validation.location import ConfigLocation


def load_manifest_text(
    cache: dict[Path, str | None],
    diagnostics: Diagnostics,
    config_path: Path,
    manifest_relative_path: Path,
    location: ConfigLocation,
    *,
    check_files: bool,
) -> str | None:
    """Load and cache a manifest's raw text, resolved exactly like ``bump_images`` would.

    Cached as *text*, not a ``Processor``: every consumer (hand-rolled checks
    and the engine backstop) parses its own fresh ``Processor`` from it via
    ``open_for_editing``. A ``Processor`` is a mutable tree -- ``set_value``
    writes into it, and ``mustexist=False`` auto-vivifies as a side effect of
    merely checking -- so one shared, cached ``Processor`` (the previous
    design) would let the backstop's real write, or one config's checks,
    corrupt what a later config sees for the same file. Text is immutable
    and cheap to re-parse, so it's what's shared instead.

    Loaded (and validated to parse, via a throwaway ``open_for_editing`` call)
    at most once per run and cached by resolved path -- several configs
    commonly share a file -- with a load failure reported once and
    remembered as ``None``. A caller that gets back non-``None`` text can
    therefore call ``open_for_editing`` on it without guarding against it
    raising: that already happened once, here.
    """
    if not check_files:
        return None
    resolved = resolve_manifest_path(config_path, manifest_relative_path)
    if resolved in cache:
        return cache[resolved]

    text: str | None
    try:
        text = resolved.read_text(encoding="utf-8")
        open_for_editing(text)
    # UnicodeDecodeError subclasses ValueError, not OSError, so a manifest
    # that isn't valid UTF-8 would escape an OSError-only guard as a traceback.
    except (OSError, UnicodeDecodeError, ManifestLoadError, YAMLError) as exc:
        diagnostics.error(
            f"could not load manifest at {resolved}: {exc}",
            location=location.location,
            line=location.line,
        )
        text = None
    cache[resolved] = text
    return text
