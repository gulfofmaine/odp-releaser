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
from odp_releaser.report_metadata import extract_metadata

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
    assert outputs["image_name"] == "gmri/neracoos-mariners-dashboard"
    assert outputs["digest"] == client_payload.digest
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
    assert "Automated image bump by odp-releaser." in outputs["pr_body"]

    # The report metadata rides along in the PR body so a merge-time
    # `report-deployment --pr-body` run can finish the deployment report.
    metadata = extract_metadata(outputs["pr_body"])
    assert metadata is not None
    assert metadata.client_payload == client_payload
    assert metadata.environment is None
    assert metadata.environment_url is None
    assert outputs["environment"] == ""
    assert outputs["environment_url"] == ""
    assert outputs["reviewers"] == ""
    assert outputs["team_reviewers"] == ""


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
    assert "Automated image bump by odp-releaser." in pr_body
    assert extract_metadata(pr_body) is not None


def test_environment_per_image_config_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
defaults:
  environment: staging
  environment_url: https://staging.example.com
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      environment: production
      environment_url: https://mariners.example.com/{new_tag}
    - events: [release]
      environment: release-only
"""
    )

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    bump_images(
        config_path=config_path,
        client_payload=client_payload.model_dump_json(),
        dry_run=True,
    )

    outputs = _parse_github_output(output.read_text())
    # Only the push config matches; its own values beat the top-level ones,
    # and the URL is templated with the payload's values.
    assert outputs["environment"] == "production"
    expected_url = f"https://mariners.example.com/{client_payload.new_tag()}"
    assert outputs["environment_url"] == expected_url
    metadata = extract_metadata(outputs["pr_body"])
    assert metadata is not None
    assert metadata.environment == "production"
    assert metadata.environment_url == expected_url


def test_environment_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
defaults:
  environment: staging
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
"""
    )

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    bump_images(
        config_path=config_path,
        client_payload=client_payload.model_dump_json(),
        dry_run=True,
    )

    outputs = _parse_github_output(output.read_text())
    assert outputs["environment"] == "staging"
    assert outputs["environment_url"] == ""


def test_mixed_environments_warn_and_use_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      environment: production
    - events: [push]
      environment: staging
"""
    )

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    with caplog.at_level(logging.WARNING):
        bump_images(
            config_path=config_path,
            client_payload=client_payload.model_dump_json(),
            dry_run=True,
        )

    outputs = _parse_github_output(output.read_text())
    assert outputs["environment"] == "production"
    assert any(
        "Mixed environment values" in record.message for record in caplog.records
    )


def test_unknown_image_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/some-unconfigured-image", client_payload)

    config_path = MANIFESTS_DIR / "push" / "image_manifest.yaml"

    with pytest.raises(typer.Exit) as excinfo:
        bump_images(
            config_path=config_path,
            client_payload=client_payload.model_dump_json(),
            dry_run=True,
        )

    assert excinfo.value.exit_code == 1

    stderr = capsys.readouterr().err
    assert "gmri/some-unconfigured-image" in stderr
    assert str(config_path) in stderr
    assert "configured images" in stderr
    assert "gmri/neracoos-mariners-dashboard" in stderr


def test_image_present_with_empty_config_list_is_a_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text("images:\n  gmri/neracoos-mariners-dashboard: []\n")

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    bump_images(
        config_path=config_path,
        client_payload=client_payload.model_dump_json(),
        dry_run=True,
    )

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "false"
    assert outputs["image_name"] == "gmri/neracoos-mariners-dashboard"
    assert outputs["digest"] == client_payload.digest
    assert outputs["update_mode"] == "commit"


def _run_bump(config_path: Path, client_payload: object) -> None:
    bump_images(
        config_path=config_path,
        client_payload=client_payload.model_dump_json(),  # type: ignore[attr-defined]
        dry_run=True,
    )


def _payload_for(image_name: str = "gmri/neracoos-mariners-dashboard"):
    client_payload = load_client_payload(EventType.push)
    set_payload_image(image_name, client_payload)
    return client_payload


def test_default_allowed_source_repos_rejects_disallowed_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
defaults:
  allowed_source_repos:
    - someorg/somerepo
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
"""
    )

    client_payload = _payload_for()

    with pytest.raises(typer.Exit) as excinfo:
        _run_bump(config_path, client_payload)

    assert excinfo.value.exit_code == 1
    stderr = capsys.readouterr().err
    assert client_payload.repo in stderr
    assert client_payload.source.actor in stderr


def test_default_allowed_source_repos_allows_listed_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
defaults:
  allowed_source_repos:
    - ioos/buoy_retriever
images:
  gmri/neracoos-mariners-dashboard: []
"""
    )

    _run_bump(config_path, _payload_for())

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "false"


def test_config_allowed_source_repos_replaces_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A config's own list replaces the defaults-level one, in both directions."""
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    # The default would reject the payload's repo, but the config's own
    # list allows it.
    config_path.write_text(
        """
defaults:
  allowed_source_repos:
    - someorg/somerepo
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      allowed_source_repos:
        - ioos/buoy_retriever
"""
    )

    _run_bump(config_path, _payload_for())
    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "false"

    # And the converse: the default allows, but the config's own empty
    # list denies everyone.
    config_path.write_text(
        """
defaults:
  allowed_source_repos:
    - ioos/buoy_retriever
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      allowed_source_repos: []
"""
    )

    with pytest.raises(typer.Exit) as excinfo:
        _run_bump(config_path, _payload_for())
    assert excinfo.value.exit_code == 1


def test_allowed_actors_users_rejects_all_configs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      allowed_actors:
        users: [someone-else]
"""
    )

    client_payload = _payload_for()

    with pytest.raises(typer.Exit) as excinfo:
        _run_bump(config_path, client_payload)

    assert excinfo.value.exit_code == 1
    assert client_payload.source.actor in capsys.readouterr().err


def test_allowed_actors_filters_config_but_run_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      environment: dev
    - events: [push]
      environment: production
      allowed_actors:
        users: [someone-else]
"""
    )

    with caplog.at_level(logging.WARNING, logger="odp-releaser"):
        _run_bump(config_path, _payload_for())

    outputs = _parse_github_output(output.read_text())
    # Only the open config applies; the restricted one is skipped with a
    # warning rather than failing the run.
    assert outputs["environment"] == "dev"
    assert any(
        "not in its allowed_actors" in record.getMessage() for record in caplog.records
    )


def test_allowed_actors_users_match_case_insensitively(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    client_payload = _payload_for()
    config_path.write_text(
        f"""
defaults:
  allowed_actors:
    users: [{client_payload.source.actor.upper()}]
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
"""
    )

    _run_bump(config_path, client_payload)

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "false"


def test_allowed_actors_team_member_is_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_TOKEN", "gh_token")

    calls: list[tuple[str, str, str, str]] = []

    def fake_is_team_member(
        org: str, team_slug: str, username: str, token: str
    ) -> bool:
        calls.append((org, team_slug, username, token))
        return True

    monkeypatch.setattr("odp_releaser.bump_images.is_team_member", fake_is_team_member)

    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      allowed_actors:
        teams: [acme/deployers]
"""
    )

    client_payload = _payload_for()
    _run_bump(config_path, client_payload)

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "false"
    assert calls == [("acme", "deployers", client_payload.source.actor, "gh_token")]


def test_allowed_actors_team_non_member_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "gh_token")
    monkeypatch.setattr(
        "odp_releaser.bump_images.is_team_member",
        lambda *_args: False,
    )

    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      allowed_actors:
        teams: [acme/deployers]
"""
    )

    with pytest.raises(typer.Exit) as excinfo:
        _run_bump(config_path, _payload_for())
    assert excinfo.value.exit_code == 1


def test_allowed_actors_team_without_token_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      allowed_actors:
        teams: [acme/deployers]
"""
    )

    with pytest.raises(typer.Exit) as excinfo:
        _run_bump(config_path, _payload_for())

    assert excinfo.value.exit_code == 1
    stderr = capsys.readouterr().err
    assert "GITHUB_TOKEN" in stderr
    assert "acme/deployers" in stderr


def test_allowed_actors_malformed_team_entry_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "gh_token")

    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      allowed_actors:
        teams: [deployers]
"""
    )

    with pytest.raises(typer.Exit) as excinfo:
        _run_bump(config_path, _payload_for())

    assert excinfo.value.exit_code == 1
    assert "org/team-slug" in capsys.readouterr().err


def test_yaml_merge_key_shares_allowlists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ruamel resolves `<<: *anchor` before pydantic sees the config."""
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    client_payload = _payload_for()
    config_path.write_text(
        f"""
x-guards: &guards
  allowed_source_repos: [{client_payload.repo}]
  allowed_actors:
    users: [{client_payload.source.actor}]
images:
  gmri/neracoos-mariners-dashboard:
    - <<: *guards
      events: [push]
"""
    )

    _run_bump(config_path, client_payload)

    outputs = _parse_github_output(output.read_text())
    assert outputs["changed"] == "false"


def test_reviewers_from_defaults_flow_into_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
defaults:
  reviewers: [alice, bob]
  team_reviewers: [deployers]
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      update_mode: pull_request
"""
    )

    _run_bump(config_path, _payload_for())

    outputs = _parse_github_output(output.read_text())
    assert outputs["reviewers"] == "alice,bob"
    assert outputs["team_reviewers"] == "deployers"


def test_reviewers_config_replaces_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
defaults:
  reviewers: [alice, bob]
  team_reviewers: [deployers]
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      update_mode: pull_request
      reviewers: [carol]
      team_reviewers: []
"""
    )

    _run_bump(config_path, _payload_for())

    outputs = _parse_github_output(output.read_text())
    assert outputs["reviewers"] == "carol"
    # The config's explicit empty list replaces the default, not falls
    # back to it.
    assert outputs["team_reviewers"] == ""


def test_mixed_reviewers_warn_and_use_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      update_mode: pull_request
      reviewers: [alice]
    - events: [push]
      update_mode: pull_request
      reviewers: [bob]
"""
    )

    with caplog.at_level(logging.WARNING, logger="odp-releaser"):
        _run_bump(config_path, _payload_for())

    outputs = _parse_github_output(output.read_text())
    assert outputs["reviewers"] == "alice"
    assert any(
        "Mixed reviewers values" in record.getMessage() for record in caplog.records
    )


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


def test_bump_images_no_change_omits_commit_message_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    client_payload = load_client_payload(EventType.push)
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)
    # Match the fixture's current tag and sha so the update is a no-op.
    client_payload.tag = "5763586"
    client_payload.git_sha = "5763586f994226eb2d95b4f8b431f011fcc21f76"

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
    assert outputs["changed"] == "false"
    assert "Updated" not in outputs["commit_message"]


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
