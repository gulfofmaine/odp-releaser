"""Tests for the generated JSON Schemas in ``schemas/``.

Two things are checked: the committed schema files can't drift from the
generator (they must be byte-for-byte what `generate-config schema ...`
prints), and the generated schemas actually work as JSON Schema -- accepting
the project's own e2e fixtures, rejecting a typo'd key, and tolerating the
`x-`-prefixed YAML-anchor convention at the top level.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema
import pytest
import typer.testing
from ruamel.yaml import YAML

from odp_releaser.main import app
from odp_releaser.schemas.json_schema import (
    deploy_targets_schema,
    image_manifest_schema,
)

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).parent.parent
E2E_DIR = Path(__file__).parent / "e2e"

# (committed file, generator function, regenerate command) kept together so
# the "does the file match" test is data-driven over both schemas and can't
# silently stop covering one of them.
_SCHEMAS: dict[str, tuple[Path, Callable[[], dict[str, Any]], str]] = {
    "image_manifest": (
        REPO_ROOT / "schemas" / "image_manifest.schema.json",
        image_manifest_schema,
        "uv run odp-releaser generate-config schema image-manifest",
    ),
    "deploy_targets": (
        REPO_ROOT / "schemas" / "deploy_targets.schema.json",
        deploy_targets_schema,
        "uv run odp-releaser generate-config schema deploy-targets",
    ),
}

# CLI subcommand args for each schema, parallel to `_SCHEMAS` above.
_CLI_ARGS: dict[str, list[str]] = {
    "image_manifest": ["generate-config", "schema", "image-manifest"],
    "deploy_targets": ["generate-config", "schema", "deploy-targets"],
}


def _load_yaml(path: Path) -> object:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(_SCHEMAS))
def test_committed_schema_matches_generator(name: str) -> None:
    path, generator, command = _SCHEMAS[name]
    expected = json.dumps(generator(), indent=2) + "\n"
    actual = path.read_text(encoding="utf-8")
    relative = path.relative_to(REPO_ROOT)
    assert actual == expected, (
        f"{relative} is stale; regenerate it with:\n  {command} > {relative}"
    )


@pytest.mark.parametrize("name", sorted(_SCHEMAS))
def test_schema_command_prints_matching_schema(name: str) -> None:
    _path, generator, _command = _SCHEMAS[name]
    runner = typer.testing.CliRunner()
    result = runner.invoke(app, _CLI_ARGS[name])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == generator()


def test_image_manifest_schema_validates_e2e_fixture() -> None:
    data = _load_yaml(E2E_DIR / "image_manifest.yaml")
    jsonschema.validate(data, image_manifest_schema())


def test_deploy_targets_schema_validates_e2e_fixture() -> None:
    data = _load_yaml(E2E_DIR / "deploy_targets.yaml")
    jsonschema.validate(data, deploy_targets_schema())


def test_image_manifest_schema_rejects_typo_key() -> None:
    """A typo'd `kustomize_manifest:` (missing the trailing `s`) must fail schema validation."""
    data = {
        "images": {
            "gmri/example": [
                {
                    "events": ["push"],
                    "kustomize_manifest": [{"path": "./kustomization.yaml"}],
                }
            ]
        }
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, image_manifest_schema())


def test_image_manifest_schema_allows_top_level_x_prefixed_key() -> None:
    """A top-level `x-guards: &guards` anchor block must not be flagged as an unknown key."""
    data = {
        "x-guards": {"allowed_source_repos": ["gulfofmaine/odp-releaser"]},
        "images": {
            "gmri/example": [
                {
                    "events": ["push"],
                    "kustomize_manifests": [{"path": "./kustomization.yaml"}],
                }
            ]
        },
    }
    jsonschema.validate(data, image_manifest_schema())


def test_image_manifest_schema_allows_bare_path_shorthand() -> None:
    """`kustomize_manifests: [./kustomization.yaml]` is documented shorthand.

    `KustomizeManifest.coerce_path_string` accepts a bare path string, so the
    published schema has to as well or an editor flags a valid config.
    """
    data = _load_yaml(
        Path(__file__).parent
        / "manifests"
        / "dagster_helm_kustomize"
        / "image_manifest.yaml"
    )
    jsonschema.validate(data, image_manifest_schema())
