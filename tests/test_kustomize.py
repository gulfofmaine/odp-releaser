from pathlib import Path

from odp_releaser.manifests.kustomize import update_kustomize_with_payload
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import KustomizeManifest

KUSTOMIZATION_TEXT = """\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: mariners-dashboard-dev

resources:
  - github.com/gulfofmaine/Neracoos-1-Buoy-App/k8s?ref=5763586f994226eb2d95b4f8b431f011fcc21f76
  - image-pull-secret.yaml

images:
  - name: gmri/neracoos-mariners-dashboard
    newName: 705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/neracoos-mariners-dashboard
    newTag: "5763586"
"""

IMAGE_NAME = "gmri/neracoos-mariners-dashboard"


def _payload() -> ClientPayload:
    return ClientPayload.model_validate(
        {
            "image_name": IMAGE_NAME,
            "digest": "sha256:abc123",
            "tag": "7c8d9e0",
            "git_sha": "7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
            "image_ref": f"{IMAGE_NAME}@sha256:abc123",
            "repo": "gulfofmaine/Neracoos-1-Buoy-App",
            "source": {
                "event": "push",
                "ref": "main",
                "url": "https://github.com/gulfofmaine/Neracoos-1-Buoy-App/commit/abc",
                "run_url": "https://github.com/gulfofmaine/Neracoos-1-Buoy-App/actions/runs/1",
                "actor": "abkfenris",
            },
        }
    )


def test_tag_pin_sets_new_tag_and_leaves_no_digest() -> None:
    manifest = KustomizeManifest.model_validate({"path": "./kustomization.yaml"})
    payload = _payload()
    commit_message: list[str] = []

    result = update_kustomize_with_payload(
        Path("kustomization.yaml"),
        KUSTOMIZATION_TEXT,
        manifest,
        payload,
        commit_message,
    )

    assert "newTag: 7c8d9e0" in result
    assert "digest:" not in result


def test_digest_pin_sets_digest_and_leaves_new_tag_untouched() -> None:
    manifest = KustomizeManifest.model_validate(
        {"path": "./kustomization.yaml", "pin": "digest"}
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_kustomize_with_payload(
        Path("kustomization.yaml"),
        KUSTOMIZATION_TEXT,
        manifest,
        payload,
        commit_message,
    )

    assert "digest: sha256:abc123" in result
    # newTag is left as it was before the update -- only the pinned field
    # ("digest" here) gets written.
    assert 'newTag: "5763586"' in result


def test_digest_template_variable_is_available_in_set() -> None:
    manifest = KustomizeManifest.model_validate(
        {
            "path": "./kustomization.yaml",
            "pin": "digest",
            "set": {
                '/resources[.^"github.com/gulfofmaine/Neracoos-1-Buoy-App/k8s?ref="]': (
                    "github.com/gulfofmaine/Neracoos-1-Buoy-App/k8s?ref={digest}"
                )
            },
        }
    )
    payload = _payload()
    commit_message: list[str] = []

    result = update_kustomize_with_payload(
        Path("kustomization.yaml"),
        KUSTOMIZATION_TEXT,
        manifest,
        payload,
        commit_message,
    )

    assert "k8s?ref=sha256:abc123" in result
