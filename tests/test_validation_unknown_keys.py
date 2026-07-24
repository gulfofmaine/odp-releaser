from __future__ import annotations

import io
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from odp_releaser.schemas.manifest_config import ManifestConfig
from odp_releaser.validation.diagnostics import Diagnostics
from odp_releaser.validation.unknown_keys import report_unknown_keys

FILE = Path("image_manifest.yaml")
E2E_MANIFEST = Path(__file__).parent / "e2e" / "image_manifest.yaml"

# One unknown key planted at each level the walker is expected to recurse
# into: the document root, `defaults`, `defaults.allowed_actors`, an item of
# `images."gmri/app"`, and an item of that image's `kustomize_manifests`. A
# bare-string kustomize entry rides along to confirm it produces nothing.
NESTED_UNKNOWN_KEYS_YAML = """
unknown_top: nope
defaults:
  allowed_source_repos: [gulfofmaine/repo]
  bad_default: true
  allowed_actors:
    users: [abkfenris]
    bad_actor_key: true
images:
  gmri/app:
    - events: [push]
      bad_image_key: true
      kustomize_manifests:
        - path: ./kustomization.yaml
          bad_kustomize_key: true
        - ./bare-kustomization.yaml
"""


def _round_trip_load(text: str) -> object:
    return YAML().load(io.StringIO(text))


def _messages(diagnostics: Diagnostics) -> list[str]:
    return [d.message for d in diagnostics.diagnostics]


def _locations(diagnostics: Diagnostics) -> list[str | None]:
    return [d.location for d in diagnostics.diagnostics]


def test_unknown_keys_found_at_every_nested_level() -> None:
    data = _round_trip_load(NESTED_UNKNOWN_KEYS_YAML)
    diagnostics = Diagnostics(FILE)

    report_unknown_keys(ManifestConfig, data, diagnostics)

    messages = _messages(diagnostics)
    assert len(messages) == 5
    assert any("'unknown_top'" in m for m in messages)
    assert any("'bad_default'" in m for m in messages)
    assert any("'bad_actor_key'" in m for m in messages)
    assert any("'bad_image_key'" in m for m in messages)
    assert any("'bad_kustomize_key'" in m for m in messages)

    locations = _locations(diagnostics)
    assert None in locations  # top-level key has no containing location
    assert "defaults" in locations
    assert "defaults.allowed_actors" in locations
    assert 'images."gmri/app"[0]' in locations
    assert 'images."gmri/app"[0].kustomize_manifests[0]' in locations


def test_unknown_key_message_lists_valid_keys() -> None:
    data = _round_trip_load(NESTED_UNKNOWN_KEYS_YAML)
    diagnostics = Diagnostics(FILE)

    report_unknown_keys(ManifestConfig, data, diagnostics)

    top_level = next(d for d in diagnostics.diagnostics if d.location is None)
    assert "Unknown key 'unknown_top'" in top_level.message
    assert "valid keys are" in top_level.message
    assert "images" in top_level.message
    assert "defaults" in top_level.message


def test_bare_string_kustomize_entry_produces_no_diagnostics_for_itself() -> None:
    data = _round_trip_load(NESTED_UNKNOWN_KEYS_YAML)
    diagnostics = Diagnostics(FILE)

    report_unknown_keys(ManifestConfig, data, diagnostics)

    # Only the mapping-form kustomize entry (index 0) contributes a
    # diagnostic; the bare-string entry (index 1) contributes nothing.
    kustomize_locations = [
        loc
        for loc in _locations(diagnostics)
        if loc is not None and "kustomize_manifests" in loc
    ]
    assert kustomize_locations == ['images."gmri/app"[0].kustomize_manifests[0]']


def test_nested_line_number_is_one_based_and_correct() -> None:
    data = _round_trip_load(NESTED_UNKNOWN_KEYS_YAML)
    diagnostics = Diagnostics(FILE)

    report_unknown_keys(ManifestConfig, data, diagnostics)

    diagnostic = next(
        d for d in diagnostics.diagnostics if "bad_actor_key" in d.message
    )
    expected_line = next(
        i + 1
        for i, line in enumerate(NESTED_UNKNOWN_KEYS_YAML.splitlines())
        if "bad_actor_key:" in line
    )
    assert diagnostic.line == expected_line


@pytest.mark.parametrize(
    ("nested_key_present", "expected_error_count"),
    [
        pytest.param(False, 0, id="no-nested-x-key"),
        pytest.param(True, 1, id="nested-x-key-not-excused"),
    ],
)
def test_allowed_key_prefixes_do_not_propagate_into_recursion(
    *, nested_key_present: bool, expected_error_count: int
) -> None:
    nested_line = "      x-nested: true\n" if nested_key_present else ""
    text = f"""
x-guards: &guards
  allowed_source_repos: [gulfofmaine/repo]
images:
  gmri/app:
    - events: [push]
{nested_line}"""
    data = _round_trip_load(text)
    diagnostics = Diagnostics(FILE)

    report_unknown_keys(ManifestConfig, data, diagnostics, allowed_key_prefixes=("x-",))

    # The top-level x-guards key is always excused by the prefix; only a
    # nested x- key (not excused, since the prefix is level-local) can
    # produce a diagnostic.
    assert len(diagnostics.diagnostics) == expected_error_count
    if nested_key_present:
        assert "x-nested" in diagnostics.diagnostics[0].message
        assert diagnostics.diagnostics[0].location == 'images."gmri/app"[0]'


def test_allowed_key_prefixes_required_to_excuse_top_level_x_key() -> None:
    text = """
x-guards: &guards
  allowed_source_repos: [gulfofmaine/repo]
images:
  gmri/app:
    - events: [push]
"""
    data = _round_trip_load(text)
    diagnostics = Diagnostics(FILE)

    report_unknown_keys(ManifestConfig, data, diagnostics)  # no allowed_key_prefixes

    assert any("x-guards" in d.message for d in diagnostics.diagnostics)


def test_generated_example_config_has_zero_diagnostics() -> None:
    data = _round_trip_load(ManifestConfig.generate_yaml())
    diagnostics = Diagnostics(FILE)

    report_unknown_keys(ManifestConfig, data, diagnostics)

    assert diagnostics.diagnostics == ()


def test_e2e_manifest_has_zero_diagnostics() -> None:
    data = _round_trip_load(E2E_MANIFEST.read_text())
    diagnostics = Diagnostics(E2E_MANIFEST)

    report_unknown_keys(ManifestConfig, data, diagnostics)

    assert diagnostics.diagnostics == ()


def test_plain_dict_without_ruamel_line_info_does_not_crash() -> None:
    data = {"images": {}, "unexpected": 1}
    diagnostics = Diagnostics(FILE)

    report_unknown_keys(ManifestConfig, data, diagnostics)

    assert len(diagnostics.diagnostics) == 1
    assert diagnostics.diagnostics[0].line is None
    assert diagnostics.diagnostics[0].location is None
