from __future__ import annotations

from pathlib import Path

import pytest
from yamlpath.exceptions import YAMLPathException

from odp_releaser.manifests.helm import (
    dagster_deployment_path,
    dagster_tag_path,
    update_helm_values_with_payload,
)
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import HelmManifest

FIXTURE = Path(__file__).parent / "manifests" / "dagster_helm_kustomize" / "values.yaml"
IMAGE_NAME = "gmri/sea-eagle-brown-3crs"


def _payload(image_name: str = IMAGE_NAME) -> ClientPayload:
    return ClientPayload.model_validate(
        {
            "image_name": image_name,
            "digest": "sha256:abc123abc123abc123abc123abc123abc123",
            "tag": "9f8e7d6",
            "git_sha": "9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6c",
            "image_ref": f"{image_name}@sha256:abc123abc123abc123abc123abc123abc123",
            "repo": "gulfofmaine/NERACOOS_ERDDAP_K8S",
            "source": {
                "event": "push",
                "ref": "main",
                "url": "https://github.com/gulfofmaine/NERACOOS_ERDDAP_K8S/commit/abc",
                "run_url": "https://github.com/gulfofmaine/NERACOOS_ERDDAP_K8S/actions/runs/1",
                "actor": "abkfenris",
            },
        }
    )


# --- Selector builders (pinned exact strings) --------------------------------


def test_dagster_deployment_path_exact_string() -> None:
    assert (
        dagster_deployment_path("ghcr.io/owner/app")
        == '/deployments[image.repository="ghcr.io/owner/app"]'
    )


def test_dagster_tag_path_exact_string() -> None:
    assert (
        dagster_tag_path("ghcr.io/owner/app")
        == '/deployments[image.repository="ghcr.io/owner/app"]/image/tag'
    )


def test_dagster_tag_path_is_built_from_deployment_path() -> None:
    name = "ghcr.io/owner/app"
    assert dagster_tag_path(name) == f"{dagster_deployment_path(name)}/image/tag"


def test_dagster_user_code_updates_matching_tag_and_preserves_rest() -> None:
    values_text = FIXTURE.read_text(encoding="utf-8")
    manifest = HelmManifest.model_validate(
        {"path": "./values.yaml", "dagster_user_code": True}
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_helm_values_with_payload(
        FIXTURE, values_text, manifest, payload, IMAGE_NAME, commit_message
    )

    # The matching deployment's tag is bumped to the new tag.
    assert 'tag: "9f8e7d6"' in result
    assert 'tag: "ee1cadc"' not in result

    # Untouched keys survive intact.
    assert "neracoos-filestore-efs" in result  # volumes
    assert "readinessProbe" in result
    assert "grpc-health-check" in result
    assert "repository: gmri/sea-eagle-brown-3crs" in result
    assert "pullPolicy: IfNotPresent" in result

    # The fixture has no document-start marker, and none is added.
    assert not result.startswith("---")
    assert "\n---\n" not in result


def test_non_matching_image_raises() -> None:
    values_text = FIXTURE.read_text(encoding="utf-8")
    manifest = HelmManifest.model_validate(
        {"path": "./values.yaml", "dagster_user_code": True}
    )
    payload = _payload(image_name="gmri/not-in-this-file")
    commit_message: list[str] = []

    with pytest.raises(YAMLPathException):
        update_helm_values_with_payload(
            FIXTURE,
            values_text,
            manifest,
            payload,
            "gmri/not-in-this-file",
            commit_message,
        )


def test_set_templates_apply_to_values_file() -> None:
    values_text = FIXTURE.read_text(encoding="utf-8")
    manifest = HelmManifest.model_validate(
        {
            "path": "./values.yaml",
            "dagster_user_code": False,
            "set": {"/deployments[0]/image/tag": "{new_tag}"},
        }
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_helm_values_with_payload(
        FIXTURE, values_text, manifest, payload, IMAGE_NAME, commit_message
    )

    assert "tag: 9f8e7d6" not in result
    assert 'tag: "9f8e7d6"' in result


# --- deployed_as / mirrored image.repository ---------------------------------

MIRRORED_NAME = (
    "705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/sea-eagle-brown-3crs"
)

MIRRORED_VALUES_TEXT = f"""\
deployments:
  - name: brown-3crs
    image:
      repository: {MIRRORED_NAME}
      tag: "ee1cadc"
      pullPolicy: IfNotPresent
"""


def test_mirrored_repository_is_bumpable_when_deployed_as_is_set() -> None:
    """The case that is impossible today: image.repository holds the mirror.

    Without a ``deployed_name`` distinct from ``payload.image_name``, this
    values file's ``image.repository`` (the ECR pull-through path) never
    matches the payload's upstream name and the dagster shorthand raises.
    Passing the mirrored name as ``deployed_name`` is what makes it bumpable.
    """
    manifest = HelmManifest.model_validate(
        {"path": "./values.yaml", "dagster_user_code": True}
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_helm_values_with_payload(
        FIXTURE,
        MIRRORED_VALUES_TEXT,
        manifest,
        payload,
        MIRRORED_NAME,
        commit_message,
    )

    assert f"repository: {MIRRORED_NAME}" in result
    assert 'tag: "9f8e7d6"' in result
    assert 'tag: "ee1cadc"' not in result


def test_mirrored_repository_raises_without_deployed_as() -> None:
    """Without deployed_as, the same mirrored file still can't be bumped."""
    manifest = HelmManifest.model_validate(
        {"path": "./values.yaml", "dagster_user_code": True}
    )
    payload = _payload()

    with pytest.raises(YAMLPathException):
        update_helm_values_with_payload(
            FIXTURE, MIRRORED_VALUES_TEXT, manifest, payload, IMAGE_NAME, []
        )


def test_unmirrored_case_still_works_when_deployed_as_matches_image_name() -> None:
    """The plain (unmirrored) case still works: deployed_name == image_name."""
    values_text = FIXTURE.read_text(encoding="utf-8")
    manifest = HelmManifest.model_validate(
        {"path": "./values.yaml", "dagster_user_code": True}
    )
    payload = _payload()

    result = update_helm_values_with_payload(
        FIXTURE, values_text, manifest, payload, IMAGE_NAME, []
    )

    assert 'tag: "9f8e7d6"' in result


def test_genuine_mismatch_still_raises() -> None:
    """A deployed_name that matches nothing in the file still raises."""
    values_text = FIXTURE.read_text(encoding="utf-8")
    manifest = HelmManifest.model_validate(
        {"path": "./values.yaml", "dagster_user_code": True}
    )
    payload = _payload()

    with pytest.raises(YAMLPathException):
        update_helm_values_with_payload(
            FIXTURE, values_text, manifest, payload, "gmri/totally-different", []
        )
