"""``ConfigLocation``: a dotted config path plus the best-known source line.

Split out of :mod:`odp_releaser.validation.image_manifest` purely to keep
that module under the project's line-count budget -- the same reason
:mod:`.cross_config` and :mod:`.engine_backstop` were split out earlier (see
those modules' docstrings). Unlike that split, though, ``ConfigLocation``
wasn't private to ``image_manifest`` to begin with: four modules
(:mod:`.cross_config`, :mod:`.engine_backstop`, :mod:`.manifest_text`,
:mod:`.deployed_as`) already import it from ``image_manifest`` under
``TYPE_CHECKING`` purely to type-hint the ``location`` parameters they
receive from it. Giving it its own module removes those back-references to
a much larger module in favor of one to a small, single-purpose one -- it is
shared vocabulary across the whole validation package, not something that
belongs to ``image_manifest`` specifically.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigLocation:
    """Dotted config path plus the best-known source line for diagnostics.

    ``validate_image_configs`` works from already-validated models, which
    carry no source line info at all -- only a caller sitting on the raw
    YAML (``validate_image_manifest``) can look one up. Rather than thread a
    raw line number through every helper here, callers build one
    ``ConfigLocation`` per image (or config item) with the best line they
    could cheaply find, and every diagnostic about something nested under it
    -- a ``set`` selector, a manifest path -- inherits that same "best-known"
    line via :meth:`child`, since nothing more precise is available once
    we're working with models alone.
    """

    location: str
    line: int | None = None

    def child(self, suffix: str, *, line: int | None = None) -> ConfigLocation:
        """A location for something nested under this one.

        A ``suffix`` starting with ``[`` (a list index) is appended
        directly (``<loc>[0]``); anything else is dot-joined
        (``<loc>.kustomize_manifests``), mirroring the convention
        ``unknown_keys`` already uses for dotted config paths. ``line``
        defaults to this location's own line, since a caller building a
        child location usually has no better line than its parent's.
        """
        if suffix.startswith("["):
            new_location = f"{self.location}{suffix}"
        elif self.location:
            new_location = f"{self.location}.{suffix}"
        else:
            new_location = suffix
        return ConfigLocation(new_location, line if line is not None else self.line)
