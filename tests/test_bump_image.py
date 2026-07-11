from __future__ import annotations

import logging
from pathlib import Path

import pytest
import typer

from odp_releaser.bump_image_tester import load_client_payload, set_payload_image
from odp_releaser.bump_images import bump_images

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
    client_payload = load_client_payload("push")
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

    client_payload = load_client_payload("push")
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


def test_no_config_for_image_reports_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    client_payload = load_client_payload("push")
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

    client_payload = load_client_payload("push")
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

    client_payload = load_client_payload("push")
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

    client_payload = load_client_payload("push")
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
