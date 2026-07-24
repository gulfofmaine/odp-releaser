"""Structured validation findings, collected across a file and rendered for humans.

Config validation (unknown keys, cross-field checks in later steps, ...) can
turn up several independent problems in a single file, and later steps need
to validate several files and merge their findings before deciding whether the
overall run failed. Rather than raising on the first problem, validators
append :class:`Diagnostic` instances to a :class:`Diagnostics` collector,
which callers merge (``extend``) and then interrogate (``failed``) and print
(``render``) once every file has been walked.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class Severity(StrEnum):
    """Severity of a single :class:`Diagnostic`."""

    error = "error"  # pylint: disable=invalid-name
    warning = "warning"  # pylint: disable=invalid-name


@dataclass(frozen=True)
class Diagnostic:
    """A single validation finding, tied to the file (and optionally location) it came from."""

    severity: Severity
    message: str
    file: Path
    line: int | None = None
    # Dotted config path, e.g. ``images."gmri/app"[0].kustomize_manifests[1].set``,
    # pinpointing which value the finding is about within the file.
    location: str | None = None

    def render(self) -> str:
        """Render as pre-commit/editor-friendly ``path:line: severity: message``.

        The line is omitted (``path: severity: message``) when unknown, and
        ``location`` — a value's dotted path within the file rather than a
        text position — is appended parenthetically to the message rather
        than folded into the ``path:line`` prefix, since editors and
        pre-commit's own output parse that prefix positionally.
        """
        message = self.message
        if self.location is not None:
            message = f"{message} (at {self.location})"
        if self.line is not None:
            return f"{self.file}:{self.line}: {self.severity}: {message}"
        return f"{self.file}: {self.severity}: {message}"


class Diagnostics:
    """Ordered collector of :class:`Diagnostic` describing a single file.

    ``error``/``warning`` are the convenience methods a validator calls while
    walking one file; ``add``/``extend`` exist separately so a caller merging
    diagnostics gathered by validating several files (or by delegating to
    another :class:`Diagnostics`-based helper) doesn't have to unpack
    ``Diagnostic`` fields to do so.
    """

    def __init__(self, file: Path) -> None:
        self.file = file
        self._diagnostics: list[Diagnostic] = []

    def error(
        self, message: str, *, line: int | None = None, location: str | None = None
    ) -> None:
        self.add(
            Diagnostic(
                severity=Severity.error,
                message=message,
                file=self.file,
                line=line,
                location=location,
            )
        )

    def warning(
        self, message: str, *, line: int | None = None, location: str | None = None
    ) -> None:
        self.add(
            Diagnostic(
                severity=Severity.warning,
                message=message,
                file=self.file,
                line=line,
                location=location,
            )
        )

    def add(self, diagnostic: Diagnostic) -> None:
        self._diagnostics.append(diagnostic)

    def extend(self, diagnostics: Iterable[Diagnostic]) -> None:
        self._diagnostics.extend(diagnostics)

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return tuple(self._diagnostics)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self._diagnostics if d.severity is Severity.error)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self._diagnostics if d.severity is Severity.warning)

    def failed(self, *, strict: bool = False) -> bool:
        """Whether this collector should be treated as a failed validation run.

        Any error always fails the run. ``strict`` additionally fails on
        warning-only results, for callers (e.g. a stricter CI or pre-commit
        mode) that want warnings to block rather than merely be reported.
        There is deliberately no ``__bool__``: "is this collector non-empty"
        and "should this run fail" are different questions, and a collector
        holding only warnings must stay usable (e.g. still renders) in the
        non-strict case, so truthiness would be misleading either way it's
        defined.
        """
        if self.errors:
            return True
        return strict and bool(self.warnings)

    def render(self) -> str:
        """Render every diagnostic, one per line, in the order they were recorded."""
        return "\n".join(d.render() for d in self._diagnostics)
