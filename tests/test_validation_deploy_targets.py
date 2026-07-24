from __future__ import annotations

from pathlib import Path

from odp_releaser.schemas.dispatch import DeployTarget
from odp_releaser.validation.deploy_targets import validate_deploy_targets
from odp_releaser.validation.diagnostics import Diagnostics, Severity

E2E_TARGETS = Path(__file__).parent / "e2e" / "deploy_targets.yaml"

_DEFAULT_EVENT_TYPE = DeployTarget.model_fields["event_type"].default


def _write(tmp_path: Path, text: str, name: str = "deploy_targets.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def _messages(diagnostics: Diagnostics) -> list[str]:
    return [d.message for d in diagnostics.diagnostics]


def _locations(diagnostics: Diagnostics) -> list[str | None]:
    return [d.location for d in diagnostics.diagnostics]


# --- Dogfood fixture ----------------------------------------------------------


def test_e2e_targets_fixture_is_clean() -> None:
    diagnostics = validate_deploy_targets(E2E_TARGETS)
    assert diagnostics.diagnostics == ()


# --- Delegated to load_targets -------------------------------------------------


def test_missing_file_produces_one_error(tmp_path: Path) -> None:
    diagnostics = validate_deploy_targets(tmp_path / "nope.yaml")
    assert diagnostics.failed() is True
    assert len(diagnostics.diagnostics) == 1
    assert diagnostics.errors[0].severity is Severity.error
    assert "No deploy targets file" in diagnostics.errors[0].message


def test_empty_file_produces_one_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    assert len(diagnostics.diagnostics) == 1
    assert "No deploy targets configured" in diagnostics.errors[0].message


def test_non_list_document_produces_one_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "owner: gulfofmaine\nrepo: some-repo\n")
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    assert len(diagnostics.diagnostics) == 1


def test_schema_invalid_entry_produces_one_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "- owner: gulfofmaine\n")
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    assert len(diagnostics.diagnostics) == 1
    assert "not a valid deploy-targets file" in diagnostics.errors[0].message


def test_unparsable_yaml_produces_one_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "- owner: [\n")
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    assert len(diagnostics.diagnostics) == 1


# --- Unknown keys ---------------------------------------------------------------


def test_unknown_key_is_reported(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repoo: some-deploy-repo
  repo: some-deploy-repo
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    assert any("repoo" in m for m in _messages(diagnostics))
    assert any(loc == "[0]" for loc in _locations(diagnostics))


# --- owner/repo shape -------------------------------------------------------------


def test_repo_containing_owner_slash_repo_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repo: gulfofmaine/some-deploy-repo
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    error = diagnostics.errors[0]
    assert "'/'" in error.message
    assert error.location == "[0].repo"


def test_empty_owner_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: ""
  repo: some-deploy-repo
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    error = diagnostics.errors[0]
    assert "empty" in error.message
    assert error.location == "[0].owner"


def test_whitespace_only_repo_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repo: "   "
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    assert any("empty" in m for m in _messages(diagnostics))


def test_bad_charset_owner_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulf of maine!
  repo: some-deploy-repo
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    error = next(e for e in diagnostics.errors if e.location == "[0].owner")
    assert "does not allow" in error.message


def test_bad_charset_repo_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repo: "some repo!"
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    error = next(e for e in diagnostics.errors if e.location == "[0].repo")
    assert "does not allow" in error.message


def test_owner_starting_with_hyphen_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: "-gulfofmaine"
  repo: some-deploy-repo
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    assert any("start or end" in m for m in _messages(diagnostics))


def test_owner_ending_with_hyphen_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: "gulfofmaine-"
  repo: some-deploy-repo
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    assert any("start or end" in m for m in _messages(diagnostics))


def test_empty_event_type_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repo: some-deploy-repo
  event_type: "   "
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is True
    error = next(e for e in diagnostics.errors if e.location == "[0].event_type")
    assert "empty" in error.message


# --- Warnings ---------------------------------------------------------------------


def test_duplicate_triple_is_a_warning(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repo: some-deploy-repo
- owner: gulfofmaine
  repo: some-deploy-repo
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.failed() is False
    warning = next(w for w in diagnostics.warnings if w.location == "[1]")
    assert "duplicates target [0]" in warning.message
    assert diagnostics.failed(strict=True) is True


def test_different_event_type_does_not_count_as_duplicate(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repo: some-deploy-repo
  event_type: custom-event
- owner: gulfofmaine
  repo: some-deploy-repo
  event_type: other-event
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert not any("duplicates" in m for m in _messages(diagnostics))


def test_custom_event_type_is_not_reported(tmp_path: Path) -> None:
    # A non-default event_type is a supported, documented setting: whether the
    # deploy repo listens for it can't be checked from here, so reporting it
    # would only be noise (and would fail --strict for every repo using one).
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repo: some-deploy-repo
  event_type: custom-event
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.diagnostics == ()


def test_default_event_type_is_not_reported(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        f"""\
- owner: gulfofmaine
  repo: some-deploy-repo
  event_type: {_DEFAULT_EVENT_TYPE}
""",
    )
    diagnostics = validate_deploy_targets(path)
    assert diagnostics.diagnostics == ()


# --- Line numbers ---------------------------------------------------------------


def test_error_carries_a_line_number(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
- owner: gulfofmaine
  repo: some-deploy-repo
- owner: ""
  repo: some-deploy-repo
""",
    )
    diagnostics = validate_deploy_targets(path)
    error = next(e for e in diagnostics.errors if e.location == "[1].owner")
    assert error.line == 3
