from __future__ import annotations

import logging
from pathlib import Path

import pytest
import typer
import typer.testing

from odp_releaser.bump_image_tester import (
    EventType,
    load_client_payload,
    set_payload_image,
)
from odp_releaser.bump_images import bump_images
from odp_releaser.main import app

MANIFESTS_DIR = Path(__file__).parent / "manifests"


def _parse_github_output(text: str) -> dict[str, str]:
    """Parse a ``GITHUB_OUTPUT`` heredoc file into a plain ``{key: value}`` dict."""
    result: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        key, delimiter = lines[i].split("<<", 1)
        i += 1
        value_lines: list[str] = []
        while lines[i] != delimiter:
            value_lines.append(lines[i])
            i += 1
        result[key] = "\n".join(value_lines)
        i += 1  # Skip the delimiter line
    return result


def test_missmatched_sha_format_error():
    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    with pytest.raises(KeyError):
        bump_images(
            config_path=Path(__file__).parent
            / "manifests"
            / "key_error"
            / "image_manifest.yaml",
            client_payload=client_payload.model_dump_json(),
            dry_run=True,
        )


def test_success_path_writes_github_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    bump_images(
        config_path=MANIFESTS_DIR / "push" / "image_manifest.yaml",
        client_payload=client_payload.model_dump_json(),
        dry_run=True,
    )

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "true"
    assert outputs["update_mode"] == "commit"
    assert (
        outputs["branch_name"] == "odp-releaser/bump-gmri-neracoos-mariners-dashboard"
    )
    assert (
        "Update image gmri/neracoos-mariners-dashboard to" in outputs["commit_message"]
    )
    assert "Triggered by" in outputs["commit_message"]

    assert outputs["pr_title"] == outputs["commit_message"].splitlines()[0]
    assert outputs["pr_title"].startswith(
        "Update image gmri/neracoos-mariners-dashboard to"
    )
    pr_body_lines = outputs["pr_body"].splitlines()
    assert pr_body_lines[0] != ""
    assert "Triggered by" in outputs["pr_body"]
    assert str(client_payload.source.url) in outputs["pr_body"]
    assert outputs["pr_body"].endswith("Automated image bump by odp-releaser.")


def test_dagster_helm_and_kustomize_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/sea-eagle-brown-3crs", client_payload)

    bump_images(
        config_path=MANIFESTS_DIR / "dagster_helm_kustomize" / "image_manifest.yaml",
        client_payload=client_payload.model_dump_json(),
        dry_run=True,
    )

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "true"

    commit_message = outputs["commit_message"]
    # Both manifests are visited...
    assert "Updated kustomize manifest" in commit_message
    assert "Updated helm values" in commit_message
    # ...and both would receive the new tag (push event -> payload.tag).
    new_tag = client_payload.new_tag()
    assert f"newTag to {new_tag}" in commit_message
    assert f"image/tag to {new_tag}" in commit_message

    pr_body = outputs["pr_body"]
    assert not pr_body.startswith("\n")
    assert "Updated kustomize manifest" in pr_body
    assert "Updated helm values" in pr_body
    assert pr_body.endswith("Automated image bump by odp-releaser.")


def test_no_config_for_image_reports_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/some-unconfigured-image", client_payload)

    bump_images(
        config_path=MANIFESTS_DIR / "push" / "image_manifest.yaml",
        client_payload=client_payload.model_dump_json(),
        dry_run=True,
    )

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "false"
    assert outputs["update_mode"] == "commit"


def test_allowed_source_repos_rejects_disallowed_repo(tmp_path: Path) -> None:
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text("allowed_source_repos:\n  - someorg/somerepo\nimages: {}\n")

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    with pytest.raises(typer.Exit):
        bump_images(
            config_path=config_path,
            client_payload=client_payload.model_dump_json(),
            dry_run=True,
        )


def test_allowed_source_repos_allows_listed_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        "allowed_source_repos:\n  - ioos/buoy_retriever\nimages: {}\n"
    )

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    bump_images(
        config_path=config_path,
        client_payload=client_payload.model_dump_json(),
        dry_run=True,
    )

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "false"


def test_mixed_update_mode_warns_and_prefers_pull_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        "images:\n"
        "  gmri/neracoos-mariners-dashboard:\n"
        "    - events: [push]\n"
        "      update_mode: commit\n"
        "    - events: [push]\n"
        "      update_mode: pull_request\n"
    )

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    with caplog.at_level(logging.WARNING, logger="odp-releaser"):
        bump_images(
            config_path=config_path,
            client_payload=client_payload.model_dump_json(),
            dry_run=True,
        )

    assert any("Mixed update_mode" in record.getMessage() for record in caplog.records)
    outputs = _parse_github_output(output.read_text())
    assert outputs["update_mode"] == "pull_request"
    assert outputs["changed"] == "false"


def test_test_bump_images_command_with_flags(tmp_path: Path) -> None:
    output = tmp_path / "output"
    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        [
            "test",
            "bump-images",
            "--config-path",
            str(MANIFESTS_DIR / "push" / "image_manifest.yaml"),
            "--image-name",
            "gmri/neracoos-mariners-dashboard",
            "--event-type",
            "push",
        ],
        env={"GITHUB_OUTPUT": str(output)},
    )

    assert result.exit_code == 0, result.output
    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "true"


def test_test_bump_images_rejects_invalid_event_type_flag() -> None:
    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        [
            "test",
            "bump-images",
            "--config-path",
            str(MANIFESTS_DIR / "push" / "image_manifest.yaml"),
            "--image-name",
            "gmri/neracoos-mariners-dashboard",
            "--event-type",
            "not-a-real-event",
        ],
    )

    assert result.exit_code != 0


def test_test_bump_images_prompts_for_missing_values(tmp_path: Path) -> None:
    output = tmp_path / "output"
    config_path = MANIFESTS_DIR / "push" / "image_manifest.yaml"
    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        ["test", "bump-images"],
        input=f"{config_path}\ngmri/neracoos-mariners-dashboard\npush\n",
        env={"GITHUB_OUTPUT": str(output)},
    )

    assert result.exit_code == 0, result.output
    assert "configured images" in result.output
    assert "gmri/neracoos-mariners-dashboard" in result.output
    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "true"


def test_bump_images_payload_positional_arg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        [
            "bump-images",
            client_payload.model_dump_json(),
            "--config-path",
            str(MANIFESTS_DIR / "push" / "image_manifest.yaml"),
            "--dry-run",
        ],
        env={"GITHUB_OUTPUT": str(output)},
    )

    assert result.exit_code == 0, result.output
    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "true"


def test_bump_images_env_only_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        ["bump-images", "--dry-run"],
        env={
            "GITHUB_OUTPUT": str(output),
            "CLIENT_PAYLOAD": client_payload.model_dump_json(),
            "IMAGE_MANIFEST_CONFIG_PATH": str(
                MANIFESTS_DIR / "push" / "image_manifest.yaml"
            ),
        },
    )

    assert result.exit_code == 0, result.output
    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "true"
