"""Local mirror of the e2e jobs in ``.github/workflows/ci.yml``.

Chains ``test make-payload`` (the producer the ``e2e-payload`` CI job runs)
into ``bump-images --dry-run`` against the fixtures in ``tests/e2e/``, and
asserts the same outputs the ``e2e-assert`` CI job checks. This keeps the
fixtures and the CI assertions from drifting without needing a push.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer.testing

from odp_releaser.bump_images import bump_images
from odp_releaser.main import app
from odp_releaser.notify import load_targets
from test_bump_image import _parse_github_output

E2E_DIR = Path(__file__).parent / "e2e"
TAG = "e2e-0123456"
DIGEST = "sha256:" + "a" * 64


def _make_payload(image_name: str) -> str:
    """Generate a client payload the way the ``e2e-payload`` CI job does."""
    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        [
            "test",
            "make-payload",
            image_name,
            TAG,
            DIGEST,
            "--github-event-name",
            "workflow_dispatch",
            "--github-event-path",
            "/dev/null",
            "--github-repository",
            "gulfofmaine/odp-releaser",
            "--github-actor",
            "e2e-bot",
            "--github-run-id",
            "12345",
            "--github-ref-name",
            "main",
            "--github-sha",
            "0123456789abcdef0123456789abcdef01234567",
        ],
    )
    assert result.exit_code == 0, result.output
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("image_name", "update_mode", "manifest_name"),
    [
        (
            "ghcr.io/gulfofmaine/odp-releaser-e2e-commit",
            "commit",
            "commit.kustomization.yaml",
        ),
        (
            "ghcr.io/gulfofmaine/odp-releaser-e2e-pr",
            "pull_request",
            "pr.kustomization.yaml",
        ),
    ],
)
def test_e2e_fixture_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    image_name: str,
    update_mode: str,
    manifest_name: str,
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    bump_images(
        config_path=E2E_DIR / "image_manifest.yaml",
        client_payload=_make_payload(image_name),
        dry_run=True,
    )

    outputs = _parse_github_output(output.read_text())
    sanitized = image_name.replace("/", "-")
    assert outputs["changed"] == "true"
    assert outputs["update_mode"] == update_mode
    assert outputs["branch_name"] == f"odp-releaser/bump-{sanitized}"
    assert f"Update image {image_name} to {TAG}" in outputs["commit_message"]
    assert manifest_name in outputs["commit_message"]
    assert outputs["pr_title"] == f"Update image {image_name} to {TAG}"


def test_e2e_deploy_targets_fixture_parses() -> None:
    """The notify e2e job's deploy targets fixture stays schema-valid."""
    targets = load_targets(E2E_DIR / "deploy_targets.yaml")
    assert [(t.owner, t.repo, t.event_type) for t in targets] == [
        ("gulfofmaine", "odp-releaser-e2e-target", "image-published"),
        ("odp-e2e-org", "odp-releaser-e2e-target", "image-published-e2e"),
    ]
