from pathlib import Path

from odp_releaser.manifests.kustomize import (
    image_entry_path,
    image_pin_path,
    update_kustomize_with_payload,
)
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
            "digest": "sha256:abc123abc123abc123abc123abc123abc123",
            "tag": "7c8d9e0",
            "git_sha": "7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
            "image_ref": f"{IMAGE_NAME}@sha256:abc123abc123abc123abc123abc123abc123",
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


# --- Selector builders (pinned exact strings) --------------------------------


def test_image_entry_path_exact_string() -> None:
    assert image_entry_path("ghcr.io/owner/app") == '/images[name="ghcr.io/owner/app"]'


def test_image_pin_path_tag_exact_string() -> None:
    assert (
        image_pin_path("ghcr.io/owner/app", "tag")
        == '/images[name="ghcr.io/owner/app"]/newTag'
    )


def test_image_pin_path_digest_exact_string() -> None:
    assert (
        image_pin_path("ghcr.io/owner/app", "digest")
        == '/images[name="ghcr.io/owner/app"]/digest'
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
        IMAGE_NAME,
        commit_message,
    )

    assert 'newTag: "7c8d9e0"' in result
    assert "digest:" not in result
    assert not result.startswith("---")
    assert "\n---\n" not in result


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
        IMAGE_NAME,
        commit_message,
    )

    assert 'digest: "sha256:abc123abc123abc123abc123abc123abc123"' in result
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
        IMAGE_NAME,
        commit_message,
    )

    assert "k8s?ref=sha256:abc123abc123abc123abc123abc123abc123" in result


def test_deployed_as_does_not_change_which_images_entry_is_matched() -> None:
    """A mirrored ``deployed_name`` must not steer the ``images:`` selector.

    Kustomize's own ``newName`` field is what carries a mirrored image name;
    the ``images:`` entry itself stays keyed on the upstream
    ``payload.image_name``, so passing a different ``deployed_name`` here
    must behave identically to passing ``payload.image_name`` itself --
    otherwise a config with ``deployed_as`` set would never find its entry.
    """
    manifest = KustomizeManifest.model_validate({"path": "./kustomization.yaml"})
    payload = _payload()
    mirrored_name = (
        "705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/"
        "gmri/neracoos-mariners-dashboard"
    )

    result_with_mirror = update_kustomize_with_payload(
        Path("kustomization.yaml"),
        KUSTOMIZATION_TEXT,
        manifest,
        payload,
        mirrored_name,
        [],
    )
    result_without_mirror = update_kustomize_with_payload(
        Path("kustomization.yaml"),
        KUSTOMIZATION_TEXT,
        manifest,
        payload,
        IMAGE_NAME,
        [],
    )

    # Same output regardless of deployed_name: the selector never saw it.
    assert result_with_mirror == result_without_mirror
    assert "name: gmri/neracoos-mariners-dashboard" in result_with_mirror
    assert 'newTag: "7c8d9e0"' in result_with_mirror
