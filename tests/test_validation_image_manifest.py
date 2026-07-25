from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from odp_releaser.bump_image_tester import (
    EventType,
    load_client_payload,
    set_payload_image,
)
from odp_releaser.manifests.helm import dagster_deployment_path
from odp_releaser.manifests.kustomize import image_pin_path
from odp_releaser.schemas.manifest_config import ConfigDefaults, ImageConfig
from odp_releaser.validation.diagnostics import Diagnostics, Severity
from odp_releaser.validation.image_manifest import (
    TEMPLATE_KEYS,
    ConfigLocation,
    validate_image_configs,
    validate_image_manifest,
)

if TYPE_CHECKING:
    from odp_releaser.schemas.client_payload import ClientPayload

FIXTURES = Path(__file__).parent / "manifests"
E2E_CONFIG = Path(__file__).parent / "e2e" / "image_manifest.yaml"


def _write(tmp_path: Path, text: str, name: str = "image_manifest.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def _payload() -> ClientPayload:
    payload = load_client_payload(EventType.push)
    set_payload_image("gmri/app", payload)
    return payload


def _messages(diagnostics: Diagnostics) -> list[str]:
    return [d.message for d in diagnostics.diagnostics]


def _locations(diagnostics: Diagnostics) -> list[str | None]:
    return [d.location for d in diagnostics.diagnostics]


# --- ConfigLocation ----------------------------------------------------------


def test_config_location_child_dotted_suffix() -> None:
    location = ConfigLocation('images."gmri/app"[0]', line=5)
    child = location.child("kustomize_manifests")
    assert child.location == 'images."gmri/app"[0].kustomize_manifests'
    assert child.line == 5


def test_config_location_child_bracket_suffix() -> None:
    location = ConfigLocation('images."gmri/app"', line=3)
    child = location.child("[0]")
    assert child.location == 'images."gmri/app"[0]'
    assert child.line == 3


def test_config_location_child_overrides_line() -> None:
    location = ConfigLocation("defaults", line=1)
    child = location.child("allowed_actors", line=9)
    assert child.line == 9


def test_config_location_child_of_an_empty_base_location_is_just_the_suffix() -> None:
    """No parent to dot-join against: the child's location is the bare suffix."""
    location = ConfigLocation("")
    child = location.child("images")
    assert child.location == "images"


# --- Template keys stay in sync with ClientPayload ---------------------------


def test_template_keys_match_payload_value_format_kwargs() -> None:
    payload = _payload()
    assert set(payload.value_format_kwargs()) == TEMPLATE_KEYS


# --- File-level plumbing ------------------------------------------------------


def test_missing_file_produces_one_error(tmp_path: Path) -> None:
    diagnostics = validate_image_manifest(tmp_path / "nope.yaml")
    assert len(diagnostics.diagnostics) == 1
    assert diagnostics.errors[0].severity is Severity.error


def test_unparseable_yaml_produces_one_error_with_line(tmp_path: Path) -> None:
    path = _write(tmp_path, "images:\n  gmri/app: [\n")
    diagnostics = validate_image_manifest(path)
    assert len(diagnostics.diagnostics) == 1
    assert diagnostics.errors[0].line is not None


def test_schema_validation_error_reports_one_error_per_pydantic_error(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - update_mode: not_a_real_mode
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.failed() is True
    assert any("update_mode" in (loc or "") for loc in _locations(diagnostics))


def test_unknown_keys_are_still_reported_before_semantic_checks(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      bad_key: true
""",
    )
    diagnostics = validate_image_manifest(path)
    assert any("bad_key" in m for m in _messages(diagnostics))


def test_image_with_no_configs_matching_the_payload_event_is_skipped(
    tmp_path: Path,
) -> None:
    """When every one of an image's configs is filtered out by the payload's
    event (none of them match), there's nothing left to semantically check
    for that image -- distinct from the image itself being filtered out by
    ``payload.image_name`` a few lines above.
    """
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [release]
""",
    )
    payload = _payload()  # a "push" event payload; see load_client_payload above
    assert payload.source.event == "push"

    diagnostics = validate_image_manifest(path, payload=payload, check_files=False)

    assert diagnostics.diagnostics == ()


# --- Dogfood fixtures ----------------------------------------------------------


def test_e2e_manifest_has_zero_diagnostics() -> None:
    diagnostics = validate_image_manifest(E2E_CONFIG)
    assert diagnostics.diagnostics == ()


def test_dagster_helm_kustomize_fixture_is_clean() -> None:
    diagnostics = validate_image_manifest(
        FIXTURES / "dagster_helm_kustomize" / "image_manifest.yaml"
    )
    assert diagnostics.diagnostics == ()


def test_key_error_fixture_names_the_bad_placeholder() -> None:
    diagnostics = validate_image_manifest(
        FIXTURES / "key_error" / "image_manifest.yaml"
    )
    assert diagnostics.failed() is True
    assert any("sha" in m and "placeholder" in m for m in _messages(diagnostics))


def test_push_fixture_errors_on_missing_kustomize_file() -> None:
    diagnostics = validate_image_manifest(FIXTURES / "push" / "image_manifest.yaml")
    assert diagnostics.failed() is True
    # The message carries a resolved path, so match with the platform's own
    # separator rather than a POSIX literal.
    missing = Path("overlays") / "mariners" / "kustomization.yaml"
    assert any(str(missing) in m for m in _messages(diagnostics))


def test_push_fixture_clean_with_check_files_false() -> None:
    diagnostics = validate_image_manifest(
        FIXTURES / "push" / "image_manifest.yaml", check_files=False
    )
    assert diagnostics.errors == ()


def test_yaml_merge_key_anchor_pattern_is_clean(tmp_path: Path) -> None:
    payload = _payload()
    config_path = _write(
        tmp_path,
        f"""
x-guards: &guards
  allowed_source_repos: [{payload.repo}]
  allowed_actors:
    users: [{payload.source.actor}]
images:
  gmri/app:
    - <<: *guards
      events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
""",
    )
    (tmp_path / "kustomization.yaml").write_text(
        """
images:
  - name: gmri/app
    newTag: "old"
"""
    )

    diagnostics = validate_image_manifest(config_path, payload=payload)
    assert diagnostics.diagnostics == ()


# --- E1: image name shape -----------------------------------------------------


@pytest.mark.parametrize(
    ("image_name", "should_error"),
    [
        pytest.param("gmri/app", False, id="valid"),
        pytest.param("GMRI/App", True, id="uppercase"),
        pytest.param("gmri/app:tag", True, id="colon"),
        pytest.param("gmri/app@sha256:abc", True, id="at-sign"),
        pytest.param(" gmri/app", True, id="leading-whitespace"),
        pytest.param("", True, id="empty"),
    ],
)
def test_image_name_shape(
    tmp_path: Path, image_name: str, *, should_error: bool
) -> None:
    path = _write(
        tmp_path,
        f"""
images:
  "{image_name}":
    - events: [push]
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.failed() is should_error


# --- E2: manifest file must exist and load ------------------------------------


def test_manifest_file_exists_and_loads_cleanly(tmp_path: Path) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.errors == ()


def test_manifest_file_missing_errors(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./nope.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.failed() is True
    assert any("nope.yaml" in m for m in _messages(diagnostics))


def test_manifest_file_missing_skipped_with_check_files_false(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./nope.yaml
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert diagnostics.errors == ()


def test_manifest_file_that_is_not_yaml_errors(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("images: [foo\n")
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./bad.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.failed() is True


# --- E3/E4: yamlpath selectors -------------------------------------------------


def test_unparseable_selector_errors(tmp_path: Path) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - path: ./kustomization.yaml
          set:
            '/foo[': '{new_tag}'
""",
    )
    diagnostics = validate_image_manifest(path)
    assert any("not a valid yamlpath" in m for m in _messages(diagnostics))


def test_selector_that_does_not_resolve_errors(tmp_path: Path) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - path: ./kustomization.yaml
          set:
            '/nonexistent/path': '{new_tag}'
""",
    )
    diagnostics = validate_image_manifest(path)
    assert any("does not resolve" in m for m in _messages(diagnostics))


# --- E5/W8: templated values ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expect_error_substring"),
    [
        pytest.param("{new_tag}", None, id="valid"),
        pytest.param("{sha}", "unknown placeholder", id="unknown-key"),
        pytest.param("{}", "positional placeholder", id="empty-field"),
        pytest.param("{0}", "positional placeholder", id="numeric-field"),
        pytest.param("{payload.foo}", "attribute/index access", id="attribute-access"),
        pytest.param("{new_tag", "not a valid format string", id="stray-brace"),
    ],
)
def test_set_value_template_checks(
    tmp_path: Path, value: str, expect_error_substring: str | None
) -> None:
    (tmp_path / "values.yaml").write_text("some:\n  path: old\n")
    path = _write(
        tmp_path,
        f"""
images:
  gmri/app:
    - events: [push]
      file_manifests:
        - path: ./values.yaml
          set:
            '/some/path': "{value}"
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    if expect_error_substring is None:
        assert diagnostics.errors == ()
    else:
        assert any(expect_error_substring in m for m in _messages(diagnostics))


def test_set_value_with_no_placeholder_warns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      file_manifests:
        - path: ./values.yaml
          set:
            '/some/path': "a-constant-value"
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("no template placeholder" in m for m in _messages(diagnostics))
    assert diagnostics.failed() is False
    assert diagnostics.failed(strict=True) is True


def test_environment_url_no_placeholder_does_not_warn(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      environment_url: "https://example.com/static"
      file_manifests:
        - path: ./values.yaml
          set:
            '/some/path': "{new_tag}"
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert not any("no template placeholder" in m for m in _messages(diagnostics))


def test_environment_url_bad_placeholder_errors(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      environment_url: "https://example.com/{sha}"
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("unknown placeholder" in m for m in _messages(diagnostics))


def test_set_value_faithful_check_against_real_payload(tmp_path: Path) -> None:
    payload = _payload()
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      file_manifests:
        - path: ./values.yaml
          set:
            '/some/path': "{unknown_field}"
""",
    )
    diagnostics = validate_image_manifest(path, payload=payload, check_files=False)
    assert diagnostics.failed() is True


def test_set_value_static_checks_pass_but_real_format_call_still_fails(
    tmp_path: Path,
) -> None:
    """A field name in ``TEMPLATE_KEYS`` isn't the whole story: a format spec
    that's invalid for the placeholder's actual value (a hex tag isn't a
    float) only fails once ``value.format(**payload.value_format_kwargs())``
    is actually attempted against a real payload -- the static placeholder-name
    check alone can't catch it.
    """
    payload = _payload()
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      file_manifests:
        - path: ./values.yaml
          set:
            '/some/path': "{new_tag:.2f}"
""",
    )
    diagnostics = validate_image_manifest(path, payload=payload, check_files=False)
    assert diagnostics.failed() is True
    assert any(
        "failed to format with the real payload" in m for m in _messages(diagnostics)
    )


def test_clean_file_manifest_reaches_the_engine_backstop(tmp_path: Path) -> None:
    """A file manifest with nothing for the hand-rolled checks to flag must
    still be run through the real ``update_file_with_payload`` engine as a
    backstop -- proven here since no fixture otherwise exercises a clean
    ``file_manifests`` entry with ``check_files`` enabled.
    """
    (tmp_path / "values.yaml").write_text("some:\n  path: old\n")
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      file_manifests:
        - path: ./values.yaml
          set:
            '/some/path': "{new_tag}"
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.diagnostics == ()


# --- E6/W5/W6: kustomize pin checks --------------------------------------------


def test_pin_tag_missing_new_tag_errors(tmp_path: Path) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/other\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    assert any("newTag" in m and "pin is 'tag'" in m for m in _messages(diagnostics))


def test_pin_digest_missing_entry_warns(tmp_path: Path) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/other\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - path: ./kustomization.yaml
          pin: digest
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.errors == ()
    assert any("mustexist=False" in m for m in _messages(diagnostics))


def test_pin_tag_error_names_the_engines_own_selector(tmp_path: Path) -> None:
    """The validator's 'newTag missing' error must name the exact path the
    kustomize engine writes -- proving both sides share
    ``kustomize.image_pin_path`` rather than each spelling the selector out.
    """
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/other\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    expected_selector = image_pin_path("gmri/app", "tag")
    assert any(expected_selector in m for m in _messages(diagnostics))


def test_kustomize_both_tag_and_digest_warns(tmp_path: Path) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "1"\n    digest: "sha256:deadbeef"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    assert any("kustomize prefers digest" in m for m in _messages(diagnostics))


# --- E7/E8/E9: allowlist/reviewer shape -----------------------------------------


def test_allowed_source_repos_bad_shape_errors(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      allowed_source_repos: [bad-repo-no-slash]
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("owner/name" in m for m in _messages(diagnostics))


def test_allowed_actors_team_bad_shape_errors(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      allowed_actors:
        teams: [deployers]
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("org/team-slug" in m for m in _messages(diagnostics))


def test_team_reviewers_with_slash_errors(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      team_reviewers: ["org/deployers"]
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("bare team slug" in m for m in _messages(diagnostics))


def test_defaults_level_shape_checks_also_run(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
defaults:
  allowed_source_repos: [bad-repo-no-slash]
images:
  gmri/app:
    - events: [push]
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any(
        d.location == "defaults.allowed_source_repos" for d in diagnostics.diagnostics
    )


def test_defaults_level_environment_url_template_is_checked(tmp_path: Path) -> None:
    """``defaults.environment_url`` gets the same template check as a
    config's own ``environment_url`` -- checked once at the defaults level
    rather than only after a config resolves it.
    """
    path = _write(
        tmp_path,
        """
defaults:
  environment_url: "https://example.com/{sha}"
images:
  gmri/app:
    - events: [push]
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    error = next(
        d for d in diagnostics.diagnostics if "unknown placeholder" in d.message
    )
    assert error.location == "defaults.environment_url"


# --- W1: silent no-op config ----------------------------------------------------


def test_config_with_no_manifests_warns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("silent no-op" in m for m in _messages(diagnostics))
    assert diagnostics.failed() is False
    assert diagnostics.failed(strict=True) is True


# --- W2: unused reviewers -------------------------------------------------------


def test_reviewers_with_commit_update_mode_warns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      update_mode: commit
      reviewers: [alice]
      kustomize_manifests:
        - ./kustomization.yaml
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any(
        "never used" in m or "never be used" in m for m in _messages(diagnostics)
    )


def test_reviewers_with_pull_request_update_mode_does_not_warn(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      update_mode: pull_request
      reviewers: [alice]
      kustomize_manifests:
        - ./kustomization.yaml
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert not any(
        "never used" in m or "never be used" in m for m in _messages(diagnostics)
    )


# --- W3/W4: allowlists that deny everyone/everything ----------------------------


def test_allowed_actors_both_empty_warns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      allowed_actors: {}
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("denies every actor" in m for m in _messages(diagnostics))


def test_allowed_source_repos_explicitly_empty_warns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      allowed_source_repos: []
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("denies every source repository" in m for m in _messages(diagnostics))


# --- W7: dagster_user_code with no matching deployment --------------------------


def test_dagster_user_code_missing_deployment_warns(tmp_path: Path) -> None:
    (tmp_path / "values.yaml").write_text(
        'deployments:\n  - name: other\n    image:\n      repository: gmri/other\n      tag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      helm_charts:
        - path: ./values.yaml
          dagster_user_code: true
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.errors == ()
    assert any("dagster_user_code is true" in m for m in _messages(diagnostics))


def test_dagster_user_code_warning_names_the_engines_own_selector(
    tmp_path: Path,
) -> None:
    """The validator's 'no deployment entry' warning must name the exact
    entry selector the helm engine matches against -- proving both sides
    share ``helm.dagster_deployment_path``.
    """
    (tmp_path / "values.yaml").write_text(
        'deployments:\n  - name: other\n    image:\n      repository: gmri/other\n      tag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      helm_charts:
        - path: ./values.yaml
          dagster_user_code: true
""",
    )
    diagnostics = validate_image_manifest(path)
    expected_selector = dagster_deployment_path("gmri/app")
    assert any(expected_selector in m for m in _messages(diagnostics))


def test_dagster_user_code_with_matching_deployment_is_clean(tmp_path: Path) -> None:
    (tmp_path / "values.yaml").write_text(
        'deployments:\n  - name: app\n    image:\n      repository: gmri/app\n      tag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      helm_charts:
        - path: ./values.yaml
          dagster_user_code: true
""",
    )
    diagnostics = validate_image_manifest(path)
    assert diagnostics.diagnostics == ()


# --- W9: duplicate manifest targets ---------------------------------------------


def test_duplicate_manifest_target_within_one_config_warns(tmp_path: Path) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
        - ./kustomization.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    assert any("targeted" in m and "redundantly" in m for m in _messages(diagnostics))


def test_duplicate_manifest_target_across_configs_for_same_event_warns(
    tmp_path: Path,
) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
    - events: [push]
      update_mode: pull_request
      kustomize_manifests:
        - ./kustomization.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    assert any("targeted" in m and "redundantly" in m for m in _messages(diagnostics))


# --- W10: disagreeing settings across matching configs --------------------------


def test_disagreeing_update_mode_across_matching_configs_warns(tmp_path: Path) -> None:
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "1"\n'
    )
    (tmp_path / "other.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "1"\n'
    )
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
    - events: [push]
      update_mode: pull_request
      kustomize_manifests:
        - ./other.yaml
""",
    )
    diagnostics = validate_image_manifest(path)
    assert any("disagree on update_mode" in m for m in _messages(diagnostics))


def test_disagreeing_environment_across_matching_configs_warns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
images:
  gmri/app:
    - events: [push]
      environment: staging
    - events: [push]
      environment: production
""",
    )
    diagnostics = validate_image_manifest(path, check_files=False)
    assert any("disagree on environment" in m for m in _messages(diagnostics))


# --- validate_image_configs called directly, from models alone -----------------


def test_validate_image_configs_from_models_alone_produces_diagnostics(
    tmp_path: Path,
) -> None:
    configs = [
        ImageConfig.model_validate({"events": ["push"], "team_reviewers": ["org/bad"]}),
    ]
    diagnostics = validate_image_configs(
        tmp_path / "image_manifest.yaml",
        "gmri/app",
        configs,
        ConfigDefaults(),
        check_files=False,
    )
    assert diagnostics.failed() is True
    assert any("bare team slug" in m for m in _messages(diagnostics))


def test_validate_image_configs_appends_to_passed_diagnostics(tmp_path: Path) -> None:
    config_path = tmp_path / "image_manifest.yaml"
    diagnostics = Diagnostics(config_path)
    diagnostics.error("pre-existing problem")

    configs = [ImageConfig.model_validate({"events": ["push"]})]
    result = validate_image_configs(
        config_path,
        "gmri/app",
        configs,
        ConfigDefaults(),
        check_files=False,
        diagnostics=diagnostics,
    )

    assert result is diagnostics
    assert "pre-existing problem" in _messages(diagnostics)
    # The no-op W1 warning for the single (manifest-less) config is also present.
    assert any("silent no-op" in m for m in _messages(diagnostics))
