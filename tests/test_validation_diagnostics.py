from __future__ import annotations

from pathlib import Path

import pytest

from odp_releaser.validation.diagnostics import Diagnostic, Diagnostics, Severity

FILE = Path("config/image_manifest.yaml")


@pytest.mark.parametrize(
    ("line", "location", "expected"),
    [
        pytest.param(
            12,
            None,
            "config/image_manifest.yaml:12: error: boom",
            id="line-no-location",
        ),
        pytest.param(
            None,
            None,
            "config/image_manifest.yaml: error: boom",
            id="no-line-no-location",
        ),
        pytest.param(
            12,
            'images."gmri/app"[0].set',
            'config/image_manifest.yaml:12: error: boom (at images."gmri/app"[0].set)',
            id="line-and-location",
        ),
        pytest.param(
            None,
            'images."gmri/app"[0].set',
            'config/image_manifest.yaml: error: boom (at images."gmri/app"[0].set)',
            id="location-no-line",
        ),
    ],
)
def test_diagnostic_render_shapes(
    line: int | None, location: str | None, expected: str
) -> None:
    diagnostic = Diagnostic(
        severity=Severity.error, message="boom", file=FILE, line=line, location=location
    )
    assert diagnostic.render() == expected


def test_diagnostic_render_uses_warning_severity() -> None:
    diagnostic = Diagnostic(severity=Severity.warning, message="hmm", file=FILE)
    assert diagnostic.render() == "config/image_manifest.yaml: warning: hmm"


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
        "config/image_manifest.yaml: warning: first",
        "config/image_manifest.yaml: error: second",
        "config/image_manifest.yaml: warning: third",
    ]


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
