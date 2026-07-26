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
    DEFAULT_DEPLOYED_TEMPLATE,
    DEFAULT_STAGED_TEMPLATE,
    EXAMPLE_MANIFEST,
    CommentConfig,
    FileManifest,
    ImageConfig,
    KustomizeManifest,
    ManifestConfig,
    ResolvedComment,
    config_matches_event,
    configs_for_event,
    resolve_comment_config,
    resolve_setting,
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
    assert "# Users and teams allowed to trigger bumps" in text
    assert "# GitHub usernames, compared case-insensitively" in text
    assert "# GitHub usernames requested as reviewers on bump pull" in text
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
    # AllowedActors appears under defaults and on the first image config;
    # its field comments render only on the first occurrence.
    assert text.count("users:") == 2
    assert text.count("# GitHub usernames, compared case-insensitively") == 1


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
    config_text = (MANIFESTS_DIR / "push" / "image_manifest.yaml").read_text(
        encoding="utf-8"
    )
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


# --- config_matches_event / configs_for_event --------------------------------


def test_config_matches_event_none_matches_every_event() -> None:
    config = ImageConfig(events=None)

    assert config_matches_event(config, "push")
    assert config_matches_event(config, "release")


def test_config_matches_event_only_matches_listed_events() -> None:
    config = ImageConfig(events=["push", "publish"])

    assert config_matches_event(config, "push")
    assert config_matches_event(config, "publish")
    assert not config_matches_event(config, "release")


def test_configs_for_event_filters_and_preserves_config_order() -> None:
    matches_all = ImageConfig(events=None)
    push_only = ImageConfig(events=["push"])
    release_only = ImageConfig(events=["release"])

    result = configs_for_event([push_only, matches_all, release_only], "push")

    assert result == [push_only, matches_all]


# --- resolve_comment_config ---------------------------------------------------


def test_resolve_comment_config_falls_back_to_builtin_templates() -> None:
    resolved = resolve_comment_config(None, None)

    assert resolved == ResolvedComment(
        enabled=True,
        staged=DEFAULT_STAGED_TEMPLATE,
        deployed=DEFAULT_DEPLOYED_TEMPLATE,
    )


def test_resolve_comment_config_merges_field_by_field() -> None:
    """A config overriding one template keeps the default's other template.

    This is the whole reason the helper exists rather than reusing
    ``resolve_setting`` -- see the regression guard below.
    """
    default = CommentConfig(staged="defaults staged", deployed="defaults deployed")
    config = CommentConfig(deployed="config deployed")

    resolved = resolve_comment_config(config, default)

    assert resolved.staged == "defaults staged"
    assert resolved.deployed == "config deployed"
    assert resolved.enabled is True


def test_resolve_setting_would_drop_the_sibling_field() -> None:
    """Guard on why ``resolve_comment_config`` can't just be ``resolve_setting``.

    ``resolve_setting`` replaces a default wholesale, so the ``defaults``-level
    ``staged`` template would silently vanish the moment a config sets only
    ``deployed``.
    """
    default = CommentConfig(staged="defaults staged", deployed="defaults deployed")
    config = CommentConfig(deployed="config deployed")

    wholesale = resolve_setting(config, default)

    assert wholesale is config
    assert wholesale is not None
    assert wholesale.staged is None  # the bug the per-field helper avoids


def test_resolve_comment_config_config_template_wins_over_default() -> None:
    default = CommentConfig(staged="defaults staged")
    config = CommentConfig(staged="config staged")

    resolved = resolve_comment_config(config, default)

    assert resolved.staged == "config staged"
    assert resolved.deployed == DEFAULT_DEPLOYED_TEMPLATE


def test_resolve_comment_config_inherits_disabled_from_defaults() -> None:
    resolved = resolve_comment_config(None, CommentConfig(enabled=False))

    assert resolved.enabled is False


def test_resolve_comment_config_re_enables_at_config_level() -> None:
    resolved = resolve_comment_config(
        CommentConfig(enabled=True), CommentConfig(enabled=False)
    )

    assert resolved.enabled is True


def test_resolve_comment_config_empty_template_is_not_treated_as_unset() -> None:
    """An explicit empty template replaces the default, like other settings.

    ``resolve_setting`` only inherits on ``None``; an explicit empty value is a
    deliberate override. Comment templates follow the same rule so
    ``staged: ""`` is a way to say "post nothing while staged".
    """
    resolved = resolve_comment_config(CommentConfig(staged=""), None)

    assert resolved.staged == ""
    assert resolved.deployed == DEFAULT_DEPLOYED_TEMPLATE


def test_builtin_templates_only_use_always_populated_placeholders() -> None:
    """The built-ins must never render a half-empty link or a dangling '@'.

    ``environment_url`` and ``image_ref`` can legitimately resolve to an empty
    string at bump time, so the shipped defaults stay off them; a user template
    may still use them.
    """
    for template in (DEFAULT_STAGED_TEMPLATE, DEFAULT_DEPLOYED_TEMPLATE):
        assert "{environment_url}" not in template
        assert "{digest}" not in template
