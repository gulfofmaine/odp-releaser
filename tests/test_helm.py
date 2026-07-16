from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from odp_releaser.manifests.helm import update_helm_values_with_payload
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import HelmManifest

if TYPE_CHECKING:
    import pytest

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


def test_dagster_user_code_updates_matching_tag_and_preserves_rest() -> None:
    values_text = FIXTURE.read_text()
    manifest = HelmManifest.model_validate(
        {"path": "./values.yaml", "dagster_user_code": True}
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_helm_values_with_payload(
        FIXTURE, values_text, manifest, payload, commit_message
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


def test_non_matching_image_warns_and_leaves_file_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    values_text = FIXTURE.read_text()
    manifest = HelmManifest.model_validate(
        {"path": "./values.yaml", "dagster_user_code": True}
    )
    payload = _payload(image_name="gmri/not-in-this-file")
    commit_message: list[str] = []

    with caplog.at_level(logging.WARNING, logger="odp-releaser"):
        result = update_helm_values_with_payload(
            FIXTURE, values_text, manifest, payload, commit_message
        )

    assert any(
        "gmri/not-in-this-file" in record.getMessage() for record in caplog.records
    )
    # Original tag is left untouched.
    assert 'tag: "ee1cadc"' in result


def test_set_templates_apply_to_values_file() -> None:
    values_text = FIXTURE.read_text()
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
        FIXTURE, values_text, manifest, payload, commit_message
    )

    assert "tag: 9f8e7d6" not in result
    assert 'tag: "9f8e7d6"' in result
