from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import respx
import typer.testing

from odp_releaser.main import app
from odp_releaser.make_payload import build_payload

if TYPE_CHECKING:
    from pathlib import Path

API = "https://api.github.com"

IMAGE = "ghcr.io/gulfofmaine/climatology_py_dash"
TAG = "3f52d83"
DIGEST = "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b"
SOURCE_REPO = "gulfofmaine/climatology_py_dash"
DEPLOY_REPO = "gulfofmaine/deploy-repo"
SHA = "5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c"
RUN_ID = "29046325966"


def _client_payload() -> str:
    return build_payload(
        image_name=IMAGE,
        tag=TAG,
        digest=DIGEST,
        repo=SOURCE_REPO,
        actor="abkfenris",
        run_id="123",
        server_url="https://github.com",
        ref_name="main",
        sha=SHA,
        event_name="push",
        event_data={},
        pr=None,
    ).model_dump_json()


def _env(
    tmp_path: Path,
    rsa_private_key: str,
    *,
    config: str | None = None,
    **extra: str,
) -> dict[str, str]:
    env = {
        "CLIENT_PAYLOAD": _client_payload(),
        "GITHUB_REPOSITORY": DEPLOY_REPO,
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "REPORTER_APP_ID": "123",
        "REPORTER_APP_PRIVATE_KEY": rsa_private_key,
        **extra,
    }
    if config is not None:
        config_path = tmp_path / "image_manifest.yaml"
        config_path.write_text(config)
        env["IMAGE_MANIFEST_CONFIG_PATH"] = str(config_path)
    else:
        env["IMAGE_MANIFEST_CONFIG_PATH"] = str(tmp_path / "missing.yaml")
    return env


def _mock_source_repo(router: respx.Router) -> tuple[respx.Route, respx.Route]:
    router.get("/repos/gulfofmaine/climatology_py_dash/installation").mock(
        return_value=httpx.Response(200, json={"id": 555})
    )
    router.post("/app/installations/555/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "ghs_reporter"})
    )
    deployment_route = router.post(
        "/repos/gulfofmaine/climatology_py_dash/deployments"
    ).mock(return_value=httpx.Response(201, json={"id": 4242}))
    status_route = router.post(
        "/repos/gulfofmaine/climatology_py_dash/deployments/4242/statuses"
    ).mock(return_value=httpx.Response(201, json={"id": 1}))
    return deployment_route, status_route


# --- happy paths --------------------------------------------------------------


def test_report_deployment_commit_mode(
    tmp_path: Path, rsa_private_key: str
) -> None:
    commit_url = f"https://github.com/{DEPLOY_REPO}/commit/def456"
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        deployment_route, status_route = _mock_source_repo(router)

        result = runner.invoke(
            app,
            ["report-deployment"],
            env=_env(
                tmp_path,
                rsa_private_key,
                UPDATE_MODE="commit",
                ENVIRONMENT_URL=commit_url,
            ),
        )

    assert result.exit_code == 0, result.output
    deployment_body = json.loads(deployment_route.calls.last.request.content)
    assert deployment_body["ref"] == SHA
    # With no manifest config the deploy repo slug is the environment name.
    assert deployment_body["environment"] == DEPLOY_REPO
    assert deployment_body["auto_merge"] is False
    assert deployment_body["required_contexts"] == []
    assert deployment_body["payload"] == {
        "image_ref": f"{IMAGE}@{DIGEST}",
        "deploy_repo": DEPLOY_REPO,
    }
    status_body = json.loads(status_route.calls.last.request.content)
    assert status_body["state"] == "success"
    assert status_body["environment_url"] == commit_url
    assert status_body["log_url"] == (
        f"https://github.com/{DEPLOY_REPO}/actions/runs/{RUN_ID}"
    )
    summary = (tmp_path / "summary").read_text()
    assert "success" in summary
    assert SOURCE_REPO in summary


def test_report_deployment_pull_request_mode_reports_queued(
    tmp_path: Path, rsa_private_key: str
) -> None:
    pr_url = f"https://github.com/{DEPLOY_REPO}/pull/7"
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        _, status_route = _mock_source_repo(router)

        result = runner.invoke(
            app,
            ["report-deployment"],
            env=_env(
                tmp_path,
                rsa_private_key,
                UPDATE_MODE="pull_request",
                ENVIRONMENT_URL=pr_url,
            ),
        )

    assert result.exit_code == 0, result.output
    status_body = json.loads(status_route.calls.last.request.content)
    assert status_body["state"] == "queued"
    assert status_body["environment_url"] == pr_url


def test_report_deployment_uses_configured_environment(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    config = "images: {}\nenvironment: production\n"

    with respx.mock(base_url=API) as router:
        deployment_route, _ = _mock_source_repo(router)

        result = runner.invoke(
            app,
            ["report-deployment"],
            env=_env(tmp_path, rsa_private_key, config=config),
        )

    assert result.exit_code == 0, result.output
    deployment_body = json.loads(deployment_route.calls.last.request.content)
    assert deployment_body["environment"] == "production"


# --- failure paths ------------------------------------------------------------


def test_report_deployment_missing_credentials_exits_nonzero(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    env = _env(tmp_path, rsa_private_key)
    del env["REPORTER_APP_ID"]
    del env["REPORTER_APP_PRIVATE_KEY"]

    result = runner.invoke(app, ["report-deployment"], env=env)

    assert result.exit_code != 0
    output = result.output or result.stderr
    assert "No reporter app credentials" in output
    summary = (tmp_path / "summary").read_text()
    assert "Failed to report deployment" in summary


def test_report_deployment_api_failure_exits_nonzero(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        router.get("/repos/gulfofmaine/climatology_py_dash/installation").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )
        router.post("/app/installations/555/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": "ghs_reporter"})
        )
        router.post("/repos/gulfofmaine/climatology_py_dash/deployments").mock(
            return_value=httpx.Response(409, json={"message": "Conflict"})
        )

        result = runner.invoke(
            app, ["report-deployment"], env=_env(tmp_path, rsa_private_key)
        )

    assert result.exit_code != 0
    output = result.output or result.stderr
    assert "Failed to report deployment" in output


def test_report_deployment_invalid_payload_exits_nonzero(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    env = _env(tmp_path, rsa_private_key)
    env["CLIENT_PAYLOAD"] = json.dumps({"not": "a payload"})

    result = runner.invoke(app, ["report-deployment"], env=env)

    assert result.exit_code != 0
