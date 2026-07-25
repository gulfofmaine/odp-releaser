from __future__ import annotations

from pathlib import Path

import pytest

from odp_releaser.validation.diagnostics import Diagnostic, Diagnostics, Severity

FILE = Path("config/image_manifest.yaml")


# Expectations are built from ``FILE`` rather than hardcoding a POSIX path:
# a diagnostic renders the path with the platform's own separator (which is
# what editors and pre-commit expect), so a literal would only pass on POSIX.
@pytest.mark.parametrize(
    ("line", "location", "expected_suffix"),
    [
        pytest.param(12, None, ":12: error: boom", id="line-no-location"),
        pytest.param(None, None, ": error: boom", id="no-line-no-location"),
        pytest.param(
            12,
            'images."gmri/app"[0].set',
            ':12: error: boom (at images."gmri/app"[0].set)',
            id="line-and-location",
        ),
        pytest.param(
            None,
            'images."gmri/app"[0].set',
            ': error: boom (at images."gmri/app"[0].set)',
            id="location-no-line",
        ),
    ],
)
def test_diagnostic_render_shapes(
    line: int | None, location: str | None, expected_suffix: str
) -> None:
    diagnostic = Diagnostic(
        severity=Severity.error, message="boom", file=FILE, line=line, location=location
    )
    assert diagnostic.render() == f"{FILE}{expected_suffix}"


def test_diagnostic_render_uses_warning_severity() -> None:
    diagnostic = Diagnostic(severity=Severity.warning, message="hmm", file=FILE)
    assert diagnostic.render() == f"{FILE}: warning: hmm"


# A multi-line message (e.g. a ruamel ``YAMLError``'s ``str()``) must still
# render as one line, or only the first line ends up attributed to
# ``path:line:`` and the rest becomes unparsable noise for pre-commit/editors.
def test_diagnostic_render_collapses_multiline_message() -> None:
    message = (
        'Could not parse "config/image_manifest.yaml" as YAML: while parsing a flow mapping\n'
        '  in "<unicode string>", line 6, column 16:\n'
        "            set: {bad\n"
        "                 ^ (line: 6)"
    )
    diagnostic = Diagnostic(severity=Severity.error, message=message, file=FILE, line=7)
    rendered = diagnostic.render()

    assert len(rendered.splitlines()) == 1
    assert "^ (line: 6)" in rendered
    assert rendered.startswith(f"{FILE}:7: error: Could not parse")


def test_diagnostic_render_collapses_multiline_message_with_blank_lines() -> None:
    # Blank lines and leading indentation shouldn't leave behind double spaces.
    diagnostic = Diagnostic(
        severity=Severity.error, message="first\n\n   second", file=FILE
    )
    assert diagnostic.render() == f"{FILE}: error: first second"


@pytest.mark.parametrize("strict", [False, True])
def test_failed_is_false_when_clean(strict: bool) -> None:
    diagnostics = Diagnostics(FILE)
    assert diagnostics.failed(strict=strict) is False


def test_failed_warning_only_depends_on_strict() -> None:
    diagnostics = Diagnostics(FILE)
    diagnostics.warning("careful")
    assert diagnostics.failed(strict=False) is False
    assert diagnostics.failed(strict=True) is True


@pytest.mark.parametrize("strict", [False, True])
def test_failed_is_true_with_an_error_regardless_of_strict(strict: bool) -> None:
    diagnostics = Diagnostics(FILE)
    diagnostics.error("broken")
    assert diagnostics.failed(strict=strict) is True


def test_error_and_warning_convenience_methods_set_file_and_fields() -> None:
    diagnostics = Diagnostics(FILE)
    diagnostics.error("bad key", line=3, location="images")
    diagnostics.warning("maybe bad", line=5, location="defaults")

    assert diagnostics.errors == (
        Diagnostic(
            severity=Severity.error,
            message="bad key",
            file=FILE,
            line=3,
            location="images",
        ),
    )
    assert diagnostics.warnings == (
        Diagnostic(
            severity=Severity.warning,
            message="maybe bad",
            file=FILE,
            line=5,
            location="defaults",
        ),
    )
    assert diagnostics.diagnostics == diagnostics.errors + diagnostics.warnings


def test_render_orders_diagnostics_by_insertion_order() -> None:
    diagnostics = Diagnostics(FILE)
    diagnostics.warning("first")
    diagnostics.error("second")
    diagnostics.warning("third")

    assert diagnostics.render().splitlines() == [
        f"{FILE}: warning: first",
        f"{FILE}: error: second",
        f"{FILE}: warning: third",
    ]


# The property that actually matters for pre-commit/editor output: N
# diagnostics must render as exactly N lines, even when individual messages
# are multi-line, so each line stays attributable to the diagnostic it came from.
def test_diagnostics_render_is_one_line_per_diagnostic_even_with_multiline_messages() -> (
    None
):
    diagnostics = Diagnostics(FILE)
    diagnostics.warning("first")
    diagnostics.error("second\nwith a continuation line")
    diagnostics.warning("third\n  indented continuation\nand more")

    rendered = diagnostics.render()

    assert len(rendered.splitlines()) == 3


def test_extend_merges_diagnostics_from_another_collector() -> None:
    combined = Diagnostics(FILE)
    combined.error("top-level problem")

    nested = Diagnostics(Path("config/nested.yaml"))
    nested.warning("nested problem")

    combined.extend(nested.diagnostics)

    assert [d.message for d in combined.diagnostics] == [
        "top-level problem",
        "nested problem",
    ]
    assert combined.failed() is True


def test_diagnostics_has_no_dunder_bool() -> None:
    # Diagnostics deliberately doesn't define __bool__; callers must use
    # failed(...) or an explicit length check instead of truthiness.
    assert "__bool__" not in Diagnostics.__dict__
