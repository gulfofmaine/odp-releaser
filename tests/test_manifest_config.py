from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
import typer.testing
from ruamel.yaml import YAML

from odp_releaser.main import app
from odp_releaser.schemas.example_yaml import example_yaml
from odp_releaser.schemas.manifest_config import (
    EXAMPLE_MANIFEST,
    FileManifest,
    ImageConfig,
    KustomizeManifest,
    ManifestConfig,
)

MANIFESTS_DIR = Path(__file__).parent / "manifests"


def _load_yaml(text: str) -> object:
    return YAML().load(io.StringIO(text))


def test_example_yaml_attaches_field_descriptions_as_comments() -> None:
    text = example_yaml(EXAMPLE_MANIFEST)

    # Top-level model docstring is rendered as a leading comment.
    assert (
        "# Configuration for image manifests, mapping image names to their "
        "update configurations." in text
    )
    # Field descriptions appear as comments before their keys.
    assert "# Mapping of image names to their configurations" in text
    assert "# Full repo names (owner/name) allowed to trigger bumps" in text
    # Nested model field descriptions recurse.
    assert "# Whether the kustomize images entry pins the tag" in text
    assert "# Relative path to the Helm values file" in text


def test_example_yaml_emits_each_comment_only_once() -> None:
    text = example_yaml(EXAMPLE_MANIFEST)

    # ImageConfig occurs twice in the example, but its docstring and field
    # descriptions are only attached to the first occurrence.
    assert text.count("update_mode:") == 2
    assert text.count("# Whether to commit the change directly") == 1
    assert text.count("# List of GitHub events for these manifests") == 1
    assert text.count("# Configuration for an image, specifying which manifests") == 1
    # HelmManifest and FileManifest also appear twice; comments render once.
    assert text.count("dagster_user_code:") == 2
    assert text.count("# When true, update the image.tag of every entry") == 1
    assert text.count("# A generic YAML or JSON manifest updated purely") == 1


def test_example_yaml_round_trips_ignoring_comments() -> None:
    text = example_yaml(EXAMPLE_MANIFEST)
    data = _load_yaml(text)

    parsed = ManifestConfig.model_validate(data)
    assert parsed == EXAMPLE_MANIFEST


def test_example_yaml_collapses_default_kustomize_to_bare_string() -> None:
    text = example_yaml(EXAMPLE_MANIFEST)
    # A kustomize manifest with only a path renders as a bare string entry.
    assert "- ../apps/mariners/kustomization.yaml" in text
    # One with a non-default pin renders as a mapping instead.
    assert "path: apps/mariners-dev/kustomization.yaml" in text
    assert "pin: digest" in text


def test_generate_config_command_image_manifest_round_trips() -> None:
    runner = typer.testing.CliRunner()
    result = runner.invoke(app, ["generate-config", "image-manifest"])

    assert result.exit_code == 0
    data = _load_yaml(result.stdout)
    parsed = ManifestConfig.model_validate(data)
    assert parsed == EXAMPLE_MANIFEST


def test_model_validate_emits_no_info_from_foreign_loggers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Plain-pydantic models must not emit the goodconf INFO chatter.

    goodconf logged "No config file specified. Loading with environment
    variables." per nested model during ``model_validate``; the pydantic
    rewrite should be silent.
    """
    config_text = (MANIFESTS_DIR / "push" / "image_manifest.yaml").read_text()
    data = _load_yaml(config_text)

    with caplog.at_level(logging.INFO):
        ManifestConfig.model_validate(data)

    foreign_info = [
        record
        for record in caplog.records
        if record.levelno >= logging.INFO and record.name != "odp-releaser"
    ]
    assert foreign_info == []
    assert not any(
        "Loading with environment variables" in record.getMessage()
        for record in caplog.records
    )


def test_bare_string_only_collapses_when_other_fields_are_default() -> None:
    plain = KustomizeManifest.model_validate("./kustomization.yaml")
    pinned = KustomizeManifest.model_validate(
        {"path": "./kustomization.yaml", "pin": "digest"}
    )

    assert "- kustomization.yaml" in example_yaml(
        ManifestConfig(images={"img": [_image_with_kustomize(plain)]})
    )
    pinned_yaml = example_yaml(
        ManifestConfig(images={"img": [_image_with_kustomize(pinned)]})
    )
    assert "pin: digest" in pinned_yaml


def _image_with_kustomize(manifest: KustomizeManifest) -> ImageConfig:
    return ImageConfig(kustomize_manifests=[manifest])


def test_file_manifest_requires_set() -> None:
    with pytest.raises(ValueError, match="set"):
        FileManifest.model_validate({"path": "./deployment.json"})
