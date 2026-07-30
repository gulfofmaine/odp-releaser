from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from yamlpath.common import Parsers

from odp_releaser.manifests.file import update_file_with_payload
from odp_releaser.manifests.helpers import ManifestLoadError, open_for_editing
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import FileManifest

FIXTURE_DIR = Path(__file__).parent / "manifests" / "file"
IMAGE_NAME = "gmri/example"


def _payload() -> ClientPayload:
    return ClientPayload.model_validate(
        {
            "image_name": IMAGE_NAME,
            "digest": "sha256:abc123abc123abc123abc123abc123abc123",
            "tag": "7c8d9e0",
            "git_sha": "7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
            "image_ref": f"{IMAGE_NAME}@sha256:abc123abc123abc123abc123abc123abc123",
            "repo": "gulfofmaine/example",
            "source": {
                "event": "push",
                "ref": "main",
                "url": "https://github.com/gulfofmaine/example/commit/abc",
                "run_url": "https://github.com/gulfofmaine/example/actions/runs/1",
                "actor": "abkfenris",
            },
        }
    )


def test_yaml_file_manifest_applies_set_templates() -> None:
    path = FIXTURE_DIR / "deployment.yaml"
    manifest = FileManifest.model_validate(
        {
            "path": "./deployment.yaml",
            "set": {
                "/spec/template/spec/containers[0]/image": "gmri/example:{new_tag}",
                r"/metadata/annotations/gmri.org\/digest": "{digest}",
            },
        }
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_file_with_payload(
        path,
        path.read_text(encoding="utf-8"),
        manifest,
        payload,
        IMAGE_NAME,
        commit_message,
    )

    assert "image: gmri/example:7c8d9e0" in result
    assert 'gmri.org/digest: "sha256:abc123abc123abc123abc123abc123abc123"' in result


def test_json_file_manifest_stays_valid_json_with_stable_formatting() -> None:
    path = FIXTURE_DIR / "deployment.json"
    manifest = FileManifest.model_validate(
        {
            "path": "./deployment.json",
            "set": {
                "/spec/template/spec/containers[0]/image": "gmri/example:{new_tag}",
                r"/metadata/annotations/gmri.org\/digest": "{digest}",
            },
        }
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_file_with_payload(
        path,
        path.read_text(encoding="utf-8"),
        manifest,
        payload,
        IMAGE_NAME,
        commit_message,
    )

    # Output must round-trip as JSON with the templated values applied.
    parsed = json.loads(result)
    assert (
        parsed["spec"]["template"]["spec"]["containers"][0]["image"]
        == "gmri/example:7c8d9e0"
    )
    assert (
        parsed["metadata"]["annotations"]["gmri.org/digest"]
        == "sha256:abc123abc123abc123abc123abc123abc123"
    )

    # Stable 2-space indentation and a trailing newline.
    assert result.endswith("\n")
    assert '\n  "apiVersion": "apps/v1"' in result


def test_deployed_image_template_renders_the_mirrored_name() -> None:
    """``{deployed_image}`` lets a file manifest reference the deployed name.

    Unlike ``{new_tag}``/``{git_sha}``/``{digest}``, this placeholder isn't
    read off the payload -- it's ``ImageConfig.deployed_as`` when set,
    otherwise the payload's own ``image_name`` (see
    ``ImageConfig.deployed_name``).
    """
    path = FIXTURE_DIR / "deployment.yaml"
    mirrored_name = (
        "705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/example"
    )
    manifest = FileManifest.model_validate(
        {
            "path": "./deployment.yaml",
            "set": {
                "/spec/template/spec/containers[0]/image": "{deployed_image}@{digest}",
            },
        }
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_file_with_payload(
        path,
        path.read_text(encoding="utf-8"),
        manifest,
        payload,
        mirrored_name,
        commit_message,
    )

    assert (
        f"image: {mirrored_name}@sha256:abc123abc123abc123abc123abc123abc123" in result
    )


def test_deployed_image_falls_back_to_payload_name_when_deployed_as_is_unset() -> None:
    """With no ``deployed_as`` configured, ``{deployed_image}`` is the payload's own name."""
    path = FIXTURE_DIR / "deployment.yaml"
    manifest = FileManifest.model_validate(
        {
            "path": "./deployment.yaml",
            "set": {
                "/spec/template/spec/containers[0]/image": "{deployed_image}@{digest}",
            },
        }
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_file_with_payload(
        path,
        path.read_text(encoding="utf-8"),
        manifest,
        payload,
        IMAGE_NAME,
        commit_message,
    )

    assert f"image: {IMAGE_NAME}@sha256:abc123abc123abc123abc123abc123abc123" in result


def test_open_for_editing_raises_on_invalid_yaml() -> None:
    with pytest.raises(ManifestLoadError):
        open_for_editing(": invalid: {unclosed")


def test_open_for_editing_preserves_comment() -> None:
    yaml_editor = Parsers.get_yaml_editor(explicit_start=False)
    processor = open_for_editing("# my comment\nkey: value\n")
    buf = io.StringIO()
    yaml_editor.dump(processor.data, buf)
    result = buf.getvalue()
    assert "# my comment" in result
