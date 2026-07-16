from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import respx
import typer.testing

from odp_releaser.main import app
from odp_releaser.make_payload import build_payload
from odp_releaser.report_metadata import ReportMetadata, embed_metadata

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

DEPLOYMENTS_PATH = "/repos/gulfofmaine/climatology_py_dash/deployments"


def _client_payload_json() -> str:
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


def _env(tmp_path: Path, rsa_private_key: str, **extra: str) -> dict[str, str]:
    env = {
        "CLIENT_PAYLOAD": _client_payload_json(),
        "GITHUB_REPOSITORY": DEPLOY_REPO,
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "REPORTER_APP_ID": "123",
        "REPORTER_APP_PRIVATE_KEY": rsa_private_key,
    }
    env.update(extra)
    return env


def _mock_source_repo(
    router: respx.Router, *, existing_deployments: list[dict[str, int]] | None = None
) -> tuple[respx.Route, respx.Route, respx.Route]:
    router.get("/repos/gulfofmaine/climatology_py_dash/installation").mock(
        return_value=httpx.Response(200, json={"id": 555})
    )
    router.post("/app/installations/555/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "ghs_reporter"})
    )
    list_route = router.get(DEPLOYMENTS_PATH).mock(
        return_value=httpx.Response(200, json=existing_deployments or [])
    )
    create_route = router.post(DEPLOYMENTS_PATH).mock(
        return_value=httpx.Response(201, json={"id": 4242})
    )
    status_route = router.post(f"{DEPLOYMENTS_PATH}/4242/statuses").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    return list_route, create_route, status_route


# --- happy paths --------------------------------------------------------------


def test_report_deployment_commit_mode(tmp_path: Path, rsa_private_key: str) -> None:
    commit_url = f"https://github.com/{DEPLOY_REPO}/commit/def456"
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        list_route, create_route, status_route = _mock_source_repo(router)

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
    assert list_route.calls.last.request.url.params["sha"] == SHA
    assert list_route.calls.last.request.url.params["environment"] == DEPLOY_REPO
    deployment_body = json.loads(create_route.calls.last.request.content)
    assert deployment_body["ref"] == SHA
    # With no environment configured the deploy repo slug is the environment.
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
        _, _, status_route = _mock_source_repo(router)

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


def test_report_deployment_uses_environment_option(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        _, create_route, _ = _mock_source_repo(router)

        result = runner.invoke(
            app,
            ["report-deployment"],
            env=_env(tmp_path, rsa_private_key, ENVIRONMENT="production"),
        )

    assert result.exit_code == 0, result.output
    deployment_body = json.loads(create_route.calls.last.request.content)
    assert deployment_body["environment"] == "production"


def test_report_deployment_reuses_existing_deployment(
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
        router.get(DEPLOYMENTS_PATH).mock(
            return_value=httpx.Response(200, json=[{"id": 777}, {"id": 555}])
        )
        status_route = router.post(f"{DEPLOYMENTS_PATH}/777/statuses").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )

        result = runner.invoke(
            app, ["report-deployment"], env=_env(tmp_path, rsa_private_key)
        )

    # The newest existing deployment gets the status; none is created (a POST
    # to the deployments collection would 404 the respx router and fail).
    assert result.exit_code == 0, result.output
    status_body = json.loads(status_route.calls.last.request.content)
    assert status_body["state"] == "success"


# --- pr-body mode -------------------------------------------------------------


def _pr_body(**metadata_kwargs: str | None) -> str:
    metadata = ReportMetadata.model_validate(
        {
            "client_payload": json.loads(_client_payload_json()),
            **metadata_kwargs,
        }
    )
    return (
        "Update image gmri/example to 1.2.3\n\n"
        "Automated image bump by odp-releaser.\n\n"
        f"{embed_metadata(metadata)}"
    )


def test_report_deployment_pr_body_flips_queued_to_success(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    env = _env(
        tmp_path,
        rsa_private_key,
        PR_BODY=_pr_body(environment="production"),
        ENVIRONMENT_URL=f"https://github.com/{DEPLOY_REPO}/commit/mergesha",
    )
    del env["CLIENT_PAYLOAD"]

    with respx.mock(base_url=API) as router:
        router.get("/repos/gulfofmaine/climatology_py_dash/installation").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )
        router.post("/app/installations/555/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": "ghs_reporter"})
        )
        list_route = router.get(DEPLOYMENTS_PATH).mock(
            return_value=httpx.Response(200, json=[{"id": 777}])
        )
        status_route = router.post(f"{DEPLOYMENTS_PATH}/777/statuses").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )

        result = runner.invoke(app, ["report-deployment"], env=env)

    assert result.exit_code == 0, result.output
    # The environment embedded at bump time drives the lookup...
    assert list_route.calls.last.request.url.params["environment"] == "production"
    assert list_route.calls.last.request.url.params["sha"] == SHA
    # ...and the queued deployment from the bump is flipped to success.
    status_body = json.loads(status_route.calls.last.request.content)
    assert status_body["state"] == "success"


def test_report_deployment_pr_body_metadata_url_wins(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    env = _env(
        tmp_path,
        rsa_private_key,
        PR_BODY=_pr_body(environment_url="https://mariners.example.com"),
        ENVIRONMENT_URL=f"https://github.com/{DEPLOY_REPO}/commit/mergesha",
    )
    del env["CLIENT_PAYLOAD"]

    with respx.mock(base_url=API) as router:
        _, _, status_route = _mock_source_repo(router)

        result = runner.invoke(app, ["report-deployment"], env=env)

    assert result.exit_code == 0, result.output
    status_body = json.loads(status_route.calls.last.request.content)
    assert status_body["environment_url"] == "https://mariners.example.com"


def test_report_deployment_pr_body_without_metadata_is_a_noop(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    env = _env(tmp_path, rsa_private_key, PR_BODY="Just a regular pull request.")
    del env["CLIENT_PAYLOAD"]

    # No respx mock: a no-op must make no API calls at all.
    result = runner.invoke(app, ["report-deployment"], env=env)

    assert result.exit_code == 0, result.output
    assert "nothing to report" in result.output
    summary = (tmp_path / "summary").read_text()
    assert "nothing to report" in summary


def test_report_deployment_pr_body_with_malformed_metadata_fails(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    env = _env(
        tmp_path,
        rsa_private_key,
        PR_BODY="<!-- odp-releaser:report-deployment {not json} -->",
    )
    del env["CLIENT_PAYLOAD"]

    result = runner.invoke(app, ["report-deployment"], env=env)

    assert result.exit_code != 0
    output = result.output or result.stderr
    assert "Malformed odp-releaser report metadata" in output


# --- input validation ---------------------------------------------------------


def test_report_deployment_requires_payload_or_pr_body(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    env = _env(tmp_path, rsa_private_key)
    del env["CLIENT_PAYLOAD"]

    result = runner.invoke(app, ["report-deployment"], env=env)

    assert result.exit_code != 0
    output = result.output or result.stderr
    assert "exactly one" in output


def test_report_deployment_rejects_payload_and_pr_body(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()
    env = _env(tmp_path, rsa_private_key, PR_BODY=_pr_body())

    result = runner.invoke(app, ["report-deployment"], env=env)

    assert result.exit_code != 0
    output = result.output or result.stderr
    assert "exactly one" in output


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
        router.get(DEPLOYMENTS_PATH).mock(return_value=httpx.Response(200, json=[]))
        router.post(DEPLOYMENTS_PATH).mock(
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
