"""Semantic validation of a ``deploy_targets.yaml`` config, beyond its schema.

:func:`odp_releaser.notify.load_targets` already turns a missing file, an
empty file, unparsable YAML, or a document that doesn't match
:class:`~odp_releaser.schemas.dispatch.DeployTarget` into one of three
exceptions with good, actionable messages -- this module reuses those
messages verbatim rather than reimplementing them, since duplicating that
wording here would only let the two drift apart.

What ``load_targets`` cannot catch is a config that is *shaped* correctly but
*means* something ``notify`` will mishandle at dispatch time: ``DeployTarget``
uses pydantic's default ``extra="ignore"``, so a typo like ``repoo:``
is silently dropped and that target dispatches with no repo at all; a
``repo`` value that is actually an ``owner/repo`` pair sends
``repository_dispatch`` to a repository that doesn't exist; an ``owner`` or
``repo`` with characters GitHub doesn't allow in a name will simply never
match a real repository; and two targets with the same
``(owner, repo, event_type)`` triple mean the exact same dispatch is sent
twice. Every check below exists because of one of those specific failure
modes.
"""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from odp_releaser.notify import (
    EmptyDeployTargetsError,
    InvalidDeployTargetsError,
    MissingDeployTargetsError,
    load_targets,
)
from odp_releaser.schemas.dispatch import DeployTarget
from odp_releaser.validation.diagnostics import Diagnostics
from odp_releaser.validation.ruamel_lines import line_for_index
from odp_releaser.validation.unknown_keys import report_unknown_keys

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# GitHub only allows letters, digits, '-', '_', and '.' in an owner or
# repository name; anything else can never equal a real repository's name.
_ALLOWED_NAME_CHARS = frozenset(string.ascii_letters + string.digits + "-_.")


def validate_deploy_targets(targets_path: Path) -> Diagnostics:
    """Read, parse, schema-check, and semantically validate one targets file.

    Delegates the missing/empty/unparsable/schema-mismatch cases straight to
    :func:`~odp_releaser.notify.load_targets`, reporting its exception message
    as a single error and returning immediately -- there is no list of
    targets to walk in any of those cases. Once ``load_targets`` succeeds,
    each target is additionally checked for unknown keys (via a separate
    round-trip parse, so line numbers are available) and the semantic
    problems described in the module docstring.
    """
    diagnostics = Diagnostics(targets_path)

    # pylint: disable=duplicate-code
    try:
        targets = load_targets(targets_path)
    except (
        MissingDeployTargetsError,
        EmptyDeployTargetsError,
        InvalidDeployTargetsError,
    ) as exc:
        diagnostics.error(str(exc))
        return diagnostics
    # pylint: enable=duplicate-code

    raw_items = _load_raw_items(targets_path)
    duplicates = _duplicate_indices(targets)

    for index, target in enumerate(targets):
        location = f"[{index}]"
        line = line_for_index(raw_items, index)

        if isinstance(raw_items, list) and index < len(raw_items):
            report_unknown_keys(
                DeployTarget, raw_items[index], diagnostics, location=location
            )

        _check_owner(target.owner, location, line, diagnostics)
        _check_repo(target.repo, location, line, diagnostics)
        _check_event_type(target.event_type, location, line, diagnostics)

        if index in duplicates:
            first_index = duplicates[index]
            diagnostics.warning(
                f"target {location} duplicates target [{first_index}]'s "
                f"owner/repo/event_type ({target.owner}/{target.repo}, "
                f"event_type={target.event_type!r}); the same dispatch "
                "would be sent twice",
                location=location,
                line=line,
            )

    return diagnostics


def _load_raw_items(targets_path: Path) -> list[object] | None:
    """Round-trip-parse the targets file for source line info, if at all possible.

    ``load_targets`` already succeeded by the time this is called, so the
    file exists, is non-empty, and parses to a list matching
    ``DeployTarget`` -- but it was parsed with a ``typ="safe"`` loader, which
    carries no line/column info. This re-parses with the default round-trip
    loader to get that info; a failure here (an ``OSError``, a
    ``YAMLError``, or a document that somehow isn't a list) is not
    re-reported -- ``load_targets`` already would have raised in that case --
    it just means every diagnostic below falls back to no line number.
    """
    try:
        raw = YAML().load(targets_path.read_text(encoding="utf-8"))
    # UnicodeDecodeError subclasses ValueError, not OSError.
    except (OSError, UnicodeDecodeError, YAMLError):
        return None
    if not isinstance(raw, list):
        return None
    return raw


def _duplicate_indices(targets: Sequence[DeployTarget]) -> dict[int, int]:
    """Map each duplicate target's index to the first index with the same triple."""
    first_seen: dict[tuple[str, str, str], int] = {}
    duplicates: dict[int, int] = {}
    for index, target in enumerate(targets):
        triple = (target.owner, target.repo, target.event_type)
        if triple in first_seen:
            duplicates[index] = first_seen[triple]
        else:
            first_seen[triple] = index
    return duplicates


def _check_owner(
    owner: str, location: str, line: int | None, diagnostics: Diagnostics
) -> None:
    field_location = f"{location}.owner"
    if not owner.strip():
        diagnostics.error(
            "owner must not be empty or whitespace-only; "
            "repository_dispatch has no organization or user to target",
            location=field_location,
            line=line,
        )
        return

    _check_charset(owner, "owner", field_location, line, diagnostics)
    if owner.startswith("-") or owner.endswith("-"):
        diagnostics.error(
            f"owner {owner!r} must not start or end with '-'; GitHub does "
            "not allow that in an organization or user name, so this can "
            "never match a real owner",
            location=field_location,
            line=line,
        )


def _check_repo(
    repo: str, location: str, line: int | None, diagnostics: Diagnostics
) -> None:
    field_location = f"{location}.repo"
    if not repo.strip():
        diagnostics.error(
            "repo must not be empty or whitespace-only; repository_dispatch "
            "has no repository to target",
            location=field_location,
            line=line,
        )
        return

    if "/" in repo:
        diagnostics.error(
            f"repo {repo!r} contains '/'; put the owner in owner and only "
            "the repository name in repo, or repository_dispatch is sent "
            "to a repository that doesn't exist",
            location=field_location,
            line=line,
        )
        return

    _check_charset(repo, "repo", field_location, line, diagnostics)


def _check_charset(
    value: str,
    field: str,
    location: str,
    line: int | None,
    diagnostics: Diagnostics,
) -> None:
    invalid = sorted({char for char in value if char not in _ALLOWED_NAME_CHARS})
    if invalid:
        diagnostics.error(
            f"{field} {value!r} contains characters GitHub does not allow "
            f"in a repository owner/name ({''.join(invalid)!r}); only "
            "letters, digits, '-', '_', and '.' are permitted",
            location=location,
            line=line,
        )


def _check_event_type(
    event_type: str, location: str, line: int | None, diagnostics: Diagnostics
) -> None:
    if not event_type.strip():
        diagnostics.error(
            "event_type must not be empty or whitespace-only; "
            "repository_dispatch requires a non-empty event_type",
            location=f"{location}.event_type",
            line=line,
        )
