"""The ``odp-releaser comment`` command end to end, with GitHub mocked."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import respx
import typer.testing

from odp_releaser.comment_body import comment_marker
from odp_releaser.comment_on_pr import COMMENT_TOKEN_PERMISSIONS
from odp_releaser.github import KNOWN_TOKEN_PERMISSIONS
from odp_releaser.main import app
from odp_releaser.make_payload import build_payload
from odp_releaser.report_metadata import (
    ReportMetadata,
    embed_metadata,
    extract_metadata,
)
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.github_context import PrMerge
from odp_releaser.schemas.manifest_config import ResolvedComment

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
PR_NUMBER = 91

COMMENTS_PATH = f"/repos/{SOURCE_REPO}/issues/{PR_NUMBER}/comments"
MARKER = comment_marker(DEPLOY_REPO, IMAGE, "production")

STAGED = "staged {image_name} {new_tag} in {bump_url}"
DEPLOYED = "deployed {image_name} {new_tag} to {environment}"


def _client_payload_json(*, with_pr: bool = True) -> str:
    pr = (
        PrMerge(
            number=PR_NUMBER,
            title="Add climatology dash",
            html_url=f"https://github.com/{SOURCE_REPO}/pull/{PR_NUMBER}",
        )
        if with_pr
        else None
    )
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
        pr=pr,
    ).model_dump_json()


def _env(tmp_path: Path, rsa_private_key: str, **extra: str) -> dict[str, str]:
    env = {
        "CLIENT_PAYLOAD": _client_payload_json(),
        "GITHUB_REPOSITORY": DEPLOY_REPO,
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "REPORTER_APP_ID": "123",
        "REPORTER_APP_PRIVATE_KEY": rsa_private_key,
        "ENVIRONMENT": "production",
        "UPDATE_MODE": "pull_request",
        "BUMP_URL": f"https://github.com/{DEPLOY_REPO}/pull/7",
        "COMMENT_STAGED_TEMPLATE": STAGED,
        "COMMENT_DEPLOYED_TEMPLATE": DEPLOYED,
    }
    env.update(extra)
    return env


def _mock_source_repo(
    router: respx.Router, *, existing: list[dict[str, object]] | None = None
) -> tuple[respx.Route, respx.Route, respx.Route]:
    router.get(f"/repos/{SOURCE_REPO}/installation").mock(
        return_value=httpx.Response(200, json={"id": 555})
    )
    token_route = router.post("/app/installations/555/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "ghs_reporter"})
    )
    list_route = router.get(COMMENTS_PATH).mock(
        return_value=httpx.Response(200, json=existing or [])
    )
    create_route = router.post(COMMENTS_PATH).mock(
        return_value=httpx.Response(
            201,
            json={
                "id": 1,
                "html_url": (
                    f"https://github.com/{SOURCE_REPO}/pull/{PR_NUMBER}#issuecomment-1"
                ),
            },
        )
    )
    return token_route, create_route, list_route


# --- happy paths --------------------------------------------------------------


def test_comment_pull_request_mode_posts_the_staged_template(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        token_route, create_route, _ = _mock_source_repo(router)

        result = runner.invoke(app, ["comment"], env=_env(tmp_path, rsa_private_key))

    assert result.exit_code == 0, result.output
    body = json.loads(create_route.calls.last.request.content)["body"]
    assert body.startswith(f"staged {IMAGE} {TAG} in ")
    assert f"https://github.com/{DEPLOY_REPO}/pull/7" in body
    assert body.endswith(MARKER)
    # Only `pull_requests: write` is ever requested -- never the deployments
    # grant `report-deployment` mints separately.
    token_body = json.loads(token_route.calls.last.request.content)
    assert token_body["permissions"] == {"pull_requests": "write"}
    assert token_body["repositories"] == ["climatology_py_dash"]

    summary = (tmp_path / "summary").read_text(encoding="utf-8")
    assert "staged" in summary
    assert str(PR_NUMBER) in summary


def test_comment_commit_mode_posts_the_deployed_template(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        _, create_route, _ = _mock_source_repo(router)

        result = runner.invoke(
            app,
            ["comment"],
            env=_env(tmp_path, rsa_private_key, UPDATE_MODE="commit"),
        )

    assert result.exit_code == 0, result.output
    body = json.loads(create_route.calls.last.request.content)["body"]
    assert body.startswith(f"deployed {IMAGE} {TAG} to production")


def test_comment_updates_its_own_existing_comment(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """The bump branch gets pushed repeatedly; each run must edit one comment
    rather than pile up new ones."""
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API, assert_all_called=False) as router:
        _mock_source_repo(
            router,
            existing=[
                {
                    "id": 77,
                    "body": f"older staged text\n\n{MARKER}",
                    "html_url": f"https://github.com/{SOURCE_REPO}/pull/{PR_NUMBER}",
                }
            ],
        )
        update_route = router.patch(f"/repos/{SOURCE_REPO}/issues/comments/77").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 77,
                    "html_url": (
                        f"https://github.com/{SOURCE_REPO}/pull/"
                        f"{PR_NUMBER}#issuecomment-77"
                    ),
                },
            )
        )

        result = runner.invoke(
            app,
            ["comment"],
            env=_env(tmp_path, rsa_private_key, UPDATE_MODE="commit"),
        )

    assert result.exit_code == 0, result.output
    assert update_route.called
    body = json.loads(update_route.calls.last.request.content)["body"]
    assert body.startswith(f"deployed {IMAGE} {TAG} to production")


# --- the merge-time --pr-body path --------------------------------------------


def test_comment_from_pr_body_uses_the_embedded_settings(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """report-merged.yml never checks out the deploy repo, so the templates,
    the environment and the PR number all come out of the bump PR body."""
    metadata = ReportMetadata.model_validate_json(
        json.dumps(
            {
                "environment": "production",
                "environment_url": "https://climatology.example.com",
                "client_payload": json.loads(_client_payload_json()),
                "comment": {
                    "enabled": True,
                    "staged": STAGED,
                    "deployed": "merged {image_name} into {environment}",
                },
                "comment_pr_number": PR_NUMBER,
            }
        )
    )
    pr_body = f"Automated image bump\n\n{embed_metadata(metadata)}"
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        _, create_route, _ = _mock_source_repo(router)

        result = runner.invoke(
            app,
            ["comment"],
            env={
                "GITHUB_REPOSITORY": DEPLOY_REPO,
                "GITHUB_RUN_ID": RUN_ID,
                "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
                "REPORTER_APP_ID": "123",
                "REPORTER_APP_PRIVATE_KEY": rsa_private_key,
                "PR_BODY": pr_body,
                "UPDATE_MODE": "commit",
                "BUMP_URL": f"https://github.com/{DEPLOY_REPO}/commit/{SHA}",
            },
        )

    assert result.exit_code == 0, result.output
    body = json.loads(create_route.calls.last.request.content)["body"]
    assert body.startswith(f"merged {IMAGE} into production")
    # The marker is derived, not stored, so it still matches the bump-time one.
    assert body.endswith(MARKER)


# --- no-ops -------------------------------------------------------------------


def test_comment_is_a_no_op_without_a_source_pull_request(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        result = runner.invoke(
            app,
            ["comment"],
            env=_env(
                tmp_path,
                rsa_private_key,
                CLIENT_PAYLOAD=_client_payload_json(with_pr=False),
            ),
        )

    assert result.exit_code == 0, result.output
    assert not router.calls
    assert "no source pull request" in (tmp_path / "summary").read_text(
        encoding="utf-8"
    )


def test_comment_is_a_no_op_when_disabled(tmp_path: Path, rsa_private_key: str) -> None:
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        result = runner.invoke(
            app,
            ["comment"],
            env=_env(tmp_path, rsa_private_key, COMMENT_ENABLED="false"),
        )

    assert result.exit_code == 0, result.output
    assert not router.calls


def test_comment_is_a_no_op_for_an_empty_template(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """`staged: ""` is a deliberate "post nothing in this state"."""
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        result = runner.invoke(
            app,
            ["comment"],
            env=_env(tmp_path, rsa_private_key, COMMENT_STAGED_TEMPLATE=""),
        )

    assert result.exit_code == 0, result.output
    assert not router.calls


def test_comment_is_a_no_op_for_a_pr_body_without_metadata(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """Safe to run on any closed pull request, not just bump ones."""
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        result = runner.invoke(
            app,
            ["comment"],
            env={
                "GITHUB_REPOSITORY": DEPLOY_REPO,
                "GITHUB_RUN_ID": RUN_ID,
                "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
                "REPORTER_APP_ID": "123",
                "REPORTER_APP_PRIVATE_KEY": rsa_private_key,
                "PR_BODY": "just a human pull request",
            },
        )

    assert result.exit_code == 0, result.output
    assert not router.calls


def test_comment_from_pr_body_without_comment_settings_is_a_no_op(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """A bump PR opened by an older odp-releaser has no comment settings
    embedded; the merge-time run must not invent them."""
    metadata = ReportMetadata.model_validate_json(
        json.dumps(
            {
                "environment": "production",
                "client_payload": json.loads(_client_payload_json()),
            }
        )
    )
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        result = runner.invoke(
            app,
            ["comment"],
            env={
                "GITHUB_REPOSITORY": DEPLOY_REPO,
                "GITHUB_RUN_ID": RUN_ID,
                "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
                "REPORTER_APP_ID": "123",
                "REPORTER_APP_PRIVATE_KEY": rsa_private_key,
                "PR_BODY": f"bump\n\n{embed_metadata(metadata)}",
                "UPDATE_MODE": "commit",
            },
        )

    assert result.exit_code == 0, result.output
    assert not router.calls
    assert metadata.comment is None


# --- failures -----------------------------------------------------------------


def test_comment_requires_exactly_one_of_payload_or_pr_body(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()

    result = runner.invoke(
        app, ["comment"], env=_env(tmp_path, rsa_private_key, PR_BODY="also this")
    )

    assert result.exit_code == 1
    output = result.output or result.stderr
    assert "exactly one" in output


def test_comment_rejects_an_unknown_update_mode(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """A mis-plumbed workflow input must fail, not fall through to `deployed`.

    Announcing "deployed" for a bump that is only staged is worse than not
    commenting, so the value is an enum Typer validates rather than a string
    compared against `"pull_request"`.
    """
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        result = runner.invoke(
            app,
            ["comment"],
            env=_env(tmp_path, rsa_private_key, UPDATE_MODE="deployed"),
        )

    assert result.exit_code != 0
    output = result.output or result.stderr
    assert "commit" in output
    assert "pull_request" in output
    assert not router.calls


def test_comment_reports_an_ungranted_permission(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """Until each source org accepts the reporter app's new `Pull requests`
    permission, the mint 422s; the message has to say what to do."""
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        router.get(f"/repos/{SOURCE_REPO}/installation").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )
        router.post("/app/installations/555/access_tokens").mock(
            return_value=httpx.Response(
                422,
                json={
                    "message": (
                        "The permissions requested are not granted to this "
                        "installation."
                    )
                },
            )
        )

        result = runner.invoke(app, ["comment"], env=_env(tmp_path, rsa_private_key))

    assert result.exit_code == 1
    output = result.output or result.stderr
    assert "pull_requests" in output
    assert "accept" in output.lower()


def test_comment_reports_an_unrelated_422_in_githubs_own_words(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """A renamed or transferred source repo also 422s the token mint.

    Sending someone to accept a permission request they already accepted would
    waste their time, so this path reports what GitHub actually said instead.
    """
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        router.get(f"/repos/{SOURCE_REPO}/installation").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )
        router.post("/app/installations/555/access_tokens").mock(
            return_value=httpx.Response(
                422,
                json={
                    "message": (
                        "There is at least one repository that does not exist "
                        "or is not accessible to the parent installation."
                    )
                },
            )
        )

        result = runner.invoke(app, ["comment"], env=_env(tmp_path, rsa_private_key))

    assert result.exit_code == 1
    output = result.output or result.stderr
    assert "not accessible to the parent installation" in output
    assert "422" in output
    # No misdirection towards a permission request that isn't the problem.
    assert "accept the permission request" not in output


def test_comment_reports_a_forbidden_pull_request(
    tmp_path: Path, rsa_private_key: str
) -> None:
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API) as router:
        router.get(f"/repos/{SOURCE_REPO}/installation").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )
        router.post("/app/installations/555/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": "ghs_reporter"})
        )
        router.get(COMMENTS_PATH).mock(return_value=httpx.Response(200, json=[]))
        router.post(COMMENTS_PATH).mock(
            return_value=httpx.Response(403, json={"message": "Forbidden"})
        )

        result = runner.invoke(app, ["comment"], env=_env(tmp_path, rsa_private_key))

    assert result.exit_code == 1
    output = result.output or result.stderr
    assert SOURCE_REPO in output


def test_comment_rejects_an_unknown_placeholder(
    tmp_path: Path, rsa_private_key: str
) -> None:
    """The validator catches this ahead of time; if one slips through, the
    failure has to name the template rather than raise a bare KeyError."""
    runner = typer.testing.CliRunner()

    with respx.mock(base_url=API):
        result = runner.invoke(
            app,
            ["comment"],
            env=_env(
                tmp_path,
                rsa_private_key,
                UPDATE_MODE="commit",
                COMMENT_DEPLOYED_TEMPLATE="{nope}",
            ),
        )

    assert result.exit_code == 1
    output = result.output or result.stderr
    assert "nope" in output


def test_resolved_comment_round_trips_through_metadata() -> None:
    """Guard that the embedded settings survive the PR-body round trip."""
    resolved = ResolvedComment(enabled=True, staged=STAGED, deployed=DEPLOYED)
    metadata = ReportMetadata(
        client_payload=ClientPayload.model_validate_json(_client_payload_json()),
        comment=resolved,
        comment_pr_number=PR_NUMBER,
    )

    parsed = extract_metadata(f"body\n\n{embed_metadata(metadata)}")

    assert parsed is not None
    assert parsed.comment == resolved
    assert parsed.comment_pr_number == PR_NUMBER


def test_comment_token_permission_name_is_one_github_accepts() -> None:
    """A misspelled permission is silently dropped, and a token with no
    permissions receives every permission the installation holds -- so the
    hyphenated `pull-requests` spelling would quietly over-grant."""
    assert COMMENT_TOKEN_PERMISSIONS == {"pull_requests": "write"}
    assert set(COMMENT_TOKEN_PERMISSIONS) <= KNOWN_TOKEN_PERMISSIONS
    assert "pull-requests" not in KNOWN_TOKEN_PERMISSIONS
