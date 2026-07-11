from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import respx
import typer
import typer.testing
from pydantic import ValidationError

from odp_releaser.main import app
from odp_releaser.make_payload import build_payload

if TYPE_CHECKING:
    from pathlib import Path

API = "https://api.github.com"

IMAGE = "climatology_py_dash"
TAG = "3f52d83"
DIGEST = "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b"
REPO = "gulfofmaine/climatology_py_dash"
SHA = "5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c"


def _write_targets(path: Path, targets: list[dict[str, str]]) -> None:
    path.write_text(json.dumps(targets))


def _env(
    tmp_path: Path,
    *,
    targets_path: Path | None,
    summary_path: Path,
    **extra: str,
) -> dict[str, str]:
    event_path = tmp_path / "event.json"
    event_path.write_text("{}")
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_ACTOR": "abkfenris",
        "GITHUB_RUN_ID": "29046325966",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SHA": SHA,
        "GITHUB_STEP_SUMMARY": str(summary_path),
        **extra,
    }
    if targets_path is not None:
        env["DEPLOY_TARGETS_PATH"] = str(targets_path)
    return env


def _expected_client_payload() -> dict[str, Any]:
    return build_payload(
        image_name=IMAGE,
        tag=TAG,
        digest=DIGEST,
        image_repository="ghcr.io/gulfofmaine",
        repo=REPO,
        actor="abkfenris",
        run_id="29046325966",
        server_url="https://github.com",
        ref_name="main",
        sha=SHA,
        event_name="workflow_dispatch",
        event_data={},
        pr=None,
    ).model_dump(mode="json")


def _dispatch_apps(private_key: str, owners: list[str]) -> str:
    return json.dumps(
        {
            owner: {"app_id": str(100 + index), "private_key": private_key}
            for index, owner in enumerate(owners)
        }
    )


def _mock_target(
    router: respx.Router, owner: str, repo: str, installation_id: int
) -> respx.Route:
    router.get(f"/repos/{owner}/{repo}/installation").mock(
        return_value=httpx.Response(200, json={"id": installation_id})
    )
    router.post(f"/app/installations/{installation_id}/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": f"ghs_{owner}"})
    )
    return router.post(f"/repos/{owner}/{repo}/dispatches").mock(
        return_value=httpx.Response(204)
    )


# --- no targets --------------------------------------------------------------


def test_notify_no_targets_file(tmp_path: Path) -> None:
    summary = tmp_path / "summary"
    missing = tmp_path / "does-not-exist.json"

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        ["notify", IMAGE, TAG, DIGEST],
        env=_env(tmp_path, targets_path=missing, summary_path=summary),
    )

    assert result.exit_code == 0, result.output
    assert "No deploy targets configured" in summary.read_text()


def test_notify_empty_targets_array(tmp_path: Path) -> None:
    summary = tmp_path / "summary"
    targets = tmp_path / "deploy-targets.json"
    targets.write_text("[]")

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        ["notify", IMAGE, TAG, DIGEST],
        env=_env(tmp_path, targets_path=targets, summary_path=summary),
    )

    assert result.exit_code == 0, result.output
    assert "No deploy targets configured" in summary.read_text()


# --- successful dispatch -----------------------------------------------------


def test_notify_two_targets_succeed(tmp_path: Path, rsa_private_key: str) -> None:
    summary = tmp_path / "summary"
    targets = tmp_path / "deploy-targets.json"
    _write_targets(
        targets,
        [
            {"owner": "acme", "repo": "widgets", "event_type": "image-published"},
            {"owner": "other", "repo": "gadgets", "event_type": "image-published"},
        ],
    )

    env = _env(
        tmp_path,
        targets_path=targets,
        summary_path=summary,
        DISPATCH_APPS=_dispatch_apps(rsa_private_key, ["acme", "other"]),
    )

    with respx.mock(base_url=API) as router:
        acme_dispatch = _mock_target(router, "acme", "widgets", 12345)
        other_dispatch = _mock_target(router, "other", "gadgets", 67890)

        runner = typer.testing.CliRunner()
        result = runner.invoke(app, ["notify", IMAGE, TAG, DIGEST], env=env)

    assert result.exit_code == 0, result.output

    assert acme_dispatch.called
    assert other_dispatch.called

    expected_payload = _expected_client_payload()
    for route in (acme_dispatch, other_dispatch):
        body = json.loads(route.calls.last.request.content)
        assert body["event_type"] == "image-published"
        assert body["client_payload"] == expected_payload

    summary_text = summary.read_text()
    assert summary_text.count("| OK ") == 2
    assert "acme/widgets" in summary_text
    assert "other/gadgets" in summary_text


# --- one target fails, one succeeds ------------------------------------------


def test_notify_one_missing_credentials_still_dispatches_other(
    tmp_path: Path, rsa_private_key: str
) -> None:
    summary = tmp_path / "summary"
    targets = tmp_path / "deploy-targets.json"
    _write_targets(
        targets,
        [
            {"owner": "acme", "repo": "widgets", "event_type": "image-published"},
            {"owner": "other", "repo": "gadgets", "event_type": "image-published"},
        ],
    )

    # Only "acme" has credentials; "other" has none and no default is set.
    env = _env(
        tmp_path,
        targets_path=targets,
        summary_path=summary,
        DISPATCH_APPS=_dispatch_apps(rsa_private_key, ["acme"]),
    )

    with respx.mock(base_url=API) as router:
        acme_dispatch = _mock_target(router, "acme", "widgets", 12345)

        runner = typer.testing.CliRunner()
        result = runner.invoke(app, ["notify", IMAGE, TAG, DIGEST], env=env)

    assert result.exit_code == 1, result.output
    assert acme_dispatch.called

    summary_text = summary.read_text()
    assert summary_text.count("| OK ") == 1
    assert summary_text.count("| FAILED ") == 1
    assert "other/gadgets" in summary_text


# --- dry run -----------------------------------------------------------------


def test_notify_dry_run_makes_no_http_calls(
    tmp_path: Path, rsa_private_key: str
) -> None:
    summary = tmp_path / "summary"
    targets = tmp_path / "deploy-targets.json"
    _write_targets(
        targets,
        [
            {"owner": "acme", "repo": "widgets", "event_type": "image-published"},
            {"owner": "other", "repo": "gadgets", "event_type": "image-published"},
        ],
    )

    env = _env(
        tmp_path,
        targets_path=targets,
        summary_path=summary,
        DISPATCH_APPS=_dispatch_apps(rsa_private_key, ["acme", "other"]),
    )

    with respx.mock(base_url=API) as router:
        runner = typer.testing.CliRunner()
        result = runner.invoke(
            app, ["notify", IMAGE, TAG, DIGEST, "--dry-run"], env=env
        )

        assert router.calls.call_count == 0

    assert result.exit_code == 0, result.output
    summary_text = summary.read_text()
    assert summary_text.count("| OK ") == 2


# --- invalid targets ---------------------------------------------------------


def test_notify_malformed_json_exits_nonzero(tmp_path: Path) -> None:
    summary = tmp_path / "summary"
    targets = tmp_path / "deploy-targets.json"
    targets.write_text("{ not valid json")

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        ["notify", IMAGE, TAG, DIGEST],
        env=_env(tmp_path, targets_path=targets, summary_path=summary),
    )

    assert result.exit_code != 0
    assert "deploy-targets" in result.output
    assert not isinstance(result.exception, ValidationError)


def test_notify_schema_mismatch_exits_nonzero(tmp_path: Path) -> None:
    summary = tmp_path / "summary"
    targets = tmp_path / "deploy-targets.json"
    # Missing the required "repo" field.
    targets.write_text(json.dumps([{"owner": "acme"}]))

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        ["notify", IMAGE, TAG, DIGEST],
        env=_env(tmp_path, targets_path=targets, summary_path=summary),
    )

    assert result.exit_code != 0
    assert "deploy-targets" in result.output
    assert not isinstance(result.exception, ValidationError)
    assert result.exception is None or isinstance(
        result.exception, (typer.Exit, SystemExit)
    )
