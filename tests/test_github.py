from __future__ import annotations

import json

import httpx
import pytest
import respx
from githubkit.exception import RequestFailed

from odp_releaser.github import (
    AppNotInstalledError,
    MissingCredentialsError,
    create_deployment,
    create_deployment_status,
    installation_token_for,
    is_team_member,
    list_deployments,
    pr_for_commit,
    resolve_app_credentials,
    resolve_reporter_credentials,
    send_dispatch,
    upsert_pr_comment,
)
from odp_releaser.schemas.dispatch import DeployTarget, DispatchAppCredentials

API = "https://api.github.com"


# --- resolve_app_credentials -------------------------------------------------


def test_resolve_app_credentials_from_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DISPATCH_APPS",
        json.dumps(
            {
                "acme": {"app_id": "111", "private_key": "KEY-A"},
                "other": {"app_id": "222", "private_key": "KEY-B"},
            }
        ),
    )

    creds = resolve_app_credentials("acme")

    assert creds == DispatchAppCredentials(app_id="111", private_key="KEY-A")


def test_resolve_app_credentials_default_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPATCH_APP_ID", "999")
    monkeypatch.setenv("DISPATCH_APP_PRIVATE_KEY", "DEFAULT-KEY")

    creds = resolve_app_credentials("acme")

    assert creds == DispatchAppCredentials(app_id="999", private_key="DEFAULT-KEY")


def test_resolve_app_credentials_mapping_miss_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DISPATCH_APPS",
        json.dumps({"other": {"app_id": "222", "private_key": "KEY-B"}}),
    )
    monkeypatch.setenv("DISPATCH_APP_ID", "999")
    monkeypatch.setenv("DISPATCH_APP_PRIVATE_KEY", "DEFAULT-KEY")

    creds = resolve_app_credentials("acme")

    assert creds == DispatchAppCredentials(app_id="999", private_key="DEFAULT-KEY")


def test_resolve_app_credentials_missing_raises() -> None:
    with pytest.raises(MissingCredentialsError) as excinfo:
        resolve_app_credentials("acme")

    message = str(excinfo.value)
    assert "acme" in message
    assert "DISPATCH_APPS" in message
    assert "DISPATCH_APP_ID" in message
    assert "DISPATCH_APP_PRIVATE_KEY" in message
    assert excinfo.value.owner == "acme"


def test_resolve_app_credentials_malformed_json_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPATCH_APPS", "this is not json")

    with pytest.raises(ValueError, match="DISPATCH_APPS") as excinfo:
        resolve_app_credentials("acme")

    assert not isinstance(excinfo.value, MissingCredentialsError)


# --- resolve_reporter_credentials --------------------------------------------


def test_resolve_reporter_credentials_from_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "REPORTER_APPS",
        json.dumps({"acme": {"app_id": "111", "private_key": "KEY-A"}}),
    )

    creds = resolve_reporter_credentials("acme")

    assert creds == DispatchAppCredentials(app_id="111", private_key="KEY-A")


def test_resolve_reporter_credentials_default_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPORTER_APP_ID", "999")
    monkeypatch.setenv("REPORTER_APP_PRIVATE_KEY", "DEFAULT-KEY")

    creds = resolve_reporter_credentials("acme")

    assert creds == DispatchAppCredentials(app_id="999", private_key="DEFAULT-KEY")


def test_resolve_reporter_credentials_ignores_dispatch_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPATCH_APP_ID", "999")
    monkeypatch.setenv("DISPATCH_APP_PRIVATE_KEY", "DEFAULT-KEY")

    with pytest.raises(MissingCredentialsError) as excinfo:
        resolve_reporter_credentials("acme")

    message = str(excinfo.value)
    assert "reporter app credentials" in message
    assert "REPORTER_APPS" in message
    assert "REPORTER_APP_ID" in message
    assert "REPORTER_APP_PRIVATE_KEY" in message
    assert excinfo.value.owner == "acme"


# --- pr_for_commit -----------------------------------------------------------


def test_pr_for_commit_returns_first() -> None:
    with respx.mock(base_url=API) as router:
        router.get("/repos/acme/widgets/commits/abc123/pulls").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "number": 42,
                        "title": "Add widget",
                        "html_url": "https://github.com/acme/widgets/pull/42",
                    },
                    {
                        "number": 7,
                        "title": "Older",
                        "html_url": "https://github.com/acme/widgets/pull/7",
                    },
                ],
            )
        )

        pr = pr_for_commit("acme/widgets", "abc123", "gh_token")

    assert pr is not None
    assert pr.number == 42
    assert pr.title == "Add widget"
    assert pr.html_url == "https://github.com/acme/widgets/pull/42"


def test_pr_for_commit_empty_returns_none() -> None:
    with respx.mock(base_url=API) as router:
        router.get("/repos/acme/widgets/commits/abc123/pulls").mock(
            return_value=httpx.Response(200, json=[])
        )

        pr = pr_for_commit("acme/widgets", "abc123", "gh_token")

    assert pr is None


# --- is_team_member -----------------------------------------------------------


def test_is_team_member_active_membership() -> None:
    with respx.mock(base_url=API) as router:
        router.get("/orgs/acme/teams/deployers/memberships/octocat").mock(
            return_value=httpx.Response(200, json={"state": "active", "role": "member"})
        )

        assert is_team_member("acme", "deployers", "octocat", "gh_token") is True


def test_is_team_member_pending_membership_is_not_a_member() -> None:
    with respx.mock(base_url=API) as router:
        router.get("/orgs/acme/teams/deployers/memberships/octocat").mock(
            return_value=httpx.Response(
                200, json={"state": "pending", "role": "member"}
            )
        )

        assert is_team_member("acme", "deployers", "octocat", "gh_token") is False


def test_is_team_member_404_is_not_a_member() -> None:
    with respx.mock(base_url=API) as router:
        router.get("/orgs/acme/teams/deployers/memberships/octocat").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        assert is_team_member("acme", "deployers", "octocat", "gh_token") is False


def test_is_team_member_other_errors_propagate() -> None:
    with respx.mock(base_url=API) as router:
        router.get("/orgs/acme/teams/deployers/memberships/octocat").mock(
            return_value=httpx.Response(403, json={"message": "Forbidden"})
        )

        with pytest.raises(RequestFailed):
            is_team_member("acme", "deployers", "octocat", "gh_token")


# --- installation_token_for --------------------------------------------------


def test_installation_token_for_mints_scoped_token(rsa_private_key: str) -> None:
    creds = DispatchAppCredentials(app_id="123", private_key=rsa_private_key)

    with respx.mock(base_url=API) as router:
        router.get("/repos/acme/widgets/installation").mock(
            return_value=httpx.Response(200, json={"id": 12345})
        )
        token_route = router.post("/app/installations/12345/access_tokens").mock(
            return_value=httpx.Response(
                201,
                json={"token": "ghs_test", "expires_at": "2026-01-01T00:00:00Z"},
            )
        )

        token = installation_token_for(creds, "acme", "widgets")

    assert token == "ghs_test"
    body = json.loads(token_route.calls.last.request.content)
    assert body["repositories"] == ["widgets"]
    assert body["permissions"] == {"contents": "write"}


def test_installation_token_for_not_installed_raises(rsa_private_key: str) -> None:
    creds = DispatchAppCredentials(app_id="123", private_key=rsa_private_key)

    with respx.mock(base_url=API) as router:
        router.get("/repos/acme/widgets/installation").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        with pytest.raises(AppNotInstalledError) as excinfo:
            installation_token_for(creds, "acme", "widgets")

    message = str(excinfo.value)
    assert "acme/widgets" in message
    assert excinfo.value.owner == "acme"
    assert excinfo.value.repo == "widgets"


def test_installation_token_for_custom_permissions(rsa_private_key: str) -> None:
    creds = DispatchAppCredentials(app_id="123", private_key=rsa_private_key)

    with respx.mock(base_url=API) as router:
        router.get("/repos/acme/widgets/installation").mock(
            return_value=httpx.Response(200, json={"id": 12345})
        )
        token_route = router.post("/app/installations/12345/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": "ghs_test"})
        )

        token = installation_token_for(
            creds,
            "acme",
            "widgets",
            permissions={"deployments": "write"},
            role="reporter",
        )

    assert token == "ghs_test"
    body = json.loads(token_route.calls.last.request.content)
    assert body["permissions"] == {"deployments": "write"}


def test_installation_token_for_reporter_not_installed_message(
    rsa_private_key: str,
) -> None:
    creds = DispatchAppCredentials(app_id="123", private_key=rsa_private_key)

    with respx.mock(base_url=API) as router:
        router.get("/repos/acme/widgets/installation").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        with pytest.raises(AppNotInstalledError) as excinfo:
            installation_token_for(creds, "acme", "widgets", role="reporter")

    assert "reporter app is not installed on acme/widgets" in str(excinfo.value)


# --- create_deployment / create_deployment_status ----------------------------


def test_create_deployment_posts_expected_body() -> None:
    with respx.mock(base_url=API) as router:
        route = router.post("/repos/acme/widgets/deployments").mock(
            return_value=httpx.Response(201, json={"id": 4242})
        )

        deployment_id = create_deployment(
            "acme/widgets",
            ref="abc123",
            environment="acme/deploy-repo",
            description="d" * 200,
            token="ghs_test",
            payload={"image_ref": "ghcr.io/acme/widgets@sha256:beef"},
        )

    assert deployment_id == 4242
    body = json.loads(route.calls.last.request.content)
    assert body["ref"] == "abc123"
    assert body["environment"] == "acme/deploy-repo"
    # auto_merge must stay disabled and required_contexts empty so recording
    # the deployment never merges branches or trips on the commit's checks.
    assert body["auto_merge"] is False
    assert body["required_contexts"] == []
    assert body["payload"] == {"image_ref": "ghcr.io/acme/widgets@sha256:beef"}
    assert body["description"] == "d" * 140
    assert route.calls.last.request.headers["authorization"] == "token ghs_test"


def test_create_deployment_status_posts_expected_body() -> None:
    with respx.mock(base_url=API) as router:
        route = router.post("/repos/acme/widgets/deployments/4242/statuses").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )

        create_deployment_status(
            "acme/widgets",
            4242,
            "success",
            token="ghs_test",
            environment_url="https://github.com/acme/deploy-repo/commit/def456",
            log_url="https://github.com/acme/deploy-repo/actions/runs/99",
            description="widgets:1.2.3 in acme/deploy-repo",
        )

    body = json.loads(route.calls.last.request.content)
    assert body["state"] == "success"
    assert body["environment_url"] == (
        "https://github.com/acme/deploy-repo/commit/def456"
    )
    assert body["log_url"] == "https://github.com/acme/deploy-repo/actions/runs/99"
    assert body["description"] == "widgets:1.2.3 in acme/deploy-repo"


def test_list_deployments_filters_by_sha_and_environment() -> None:
    with respx.mock(base_url=API) as router:
        route = router.get("/repos/acme/widgets/deployments").mock(
            return_value=httpx.Response(200, json=[{"id": 99}, {"id": 42}])
        )

        ids = list_deployments(
            "acme/widgets", sha="abc123", environment="production", token="ghs_test"
        )

    assert ids == [99, 42]
    params = route.calls.last.request.url.params
    assert params["sha"] == "abc123"
    assert params["environment"] == "production"


def test_list_deployments_empty() -> None:
    with respx.mock(base_url=API) as router:
        router.get("/repos/acme/widgets/deployments").mock(
            return_value=httpx.Response(200, json=[])
        )

        ids = list_deployments(
            "acme/widgets", sha="abc123", environment="production", token="ghs_test"
        )

    assert ids == []


def test_create_deployment_status_omits_unset_urls() -> None:
    with respx.mock(base_url=API) as router:
        route = router.post("/repos/acme/widgets/deployments/4242/statuses").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )

        create_deployment_status("acme/widgets", 4242, "queued", token="ghs_test")

    body = json.loads(route.calls.last.request.content)
    assert body["state"] == "queued"
    assert "environment_url" not in body
    assert "log_url" not in body
    assert "description" not in body


# --- send_dispatch -----------------------------------------------------------


def test_send_dispatch_full_flow(
    monkeypatch: pytest.MonkeyPatch, rsa_private_key: str
) -> None:
    monkeypatch.setenv("DISPATCH_APP_ID", "123")
    monkeypatch.setenv("DISPATCH_APP_PRIVATE_KEY", rsa_private_key)

    target = DeployTarget(owner="acme", repo="widgets", event_type="image-published")
    client_payload: dict[str, object] = {"image_ref": "ghcr.io/acme/widgets:sha"}

    with respx.mock(base_url=API) as router:
        router.get("/repos/acme/widgets/installation").mock(
            return_value=httpx.Response(200, json={"id": 12345})
        )
        router.post("/app/installations/12345/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": "ghs_test"})
        )
        dispatch_route = router.post("/repos/acme/widgets/dispatches").mock(
            return_value=httpx.Response(204)
        )

        send_dispatch(target, client_payload)

    assert dispatch_route.called
    body = json.loads(dispatch_route.calls.last.request.content)
    assert body["event_type"] == "image-published"
    assert body["client_payload"] == client_payload


def test_upsert_pr_comment_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        upsert_pr_comment("acme/widgets", 1, "hi", "gh_token")
