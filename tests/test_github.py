from __future__ import annotations

import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from odp_releaser.github import (
    AppNotInstalledError,
    MissingCredentialsError,
    installation_token_for,
    pr_for_commit,
    resolve_app_credentials,
    send_dispatch,
    upsert_pr_comment,
)
from odp_releaser.schemas.dispatch import DeployTarget, DispatchAppCredentials

API = "https://api.github.com"


@pytest.fixture
def rsa_private_key() -> str:
    """A throwaway RSA private key for signing app JWTs in tests."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


@pytest.fixture(autouse=True)
def _clear_dispatch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure dispatch credential env vars never leak in from the host."""
    for name in ("DISPATCH_APPS", "DISPATCH_APP_ID", "DISPATCH_APP_PRIVATE_KEY"):
        monkeypatch.delenv(name, raising=False)


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
