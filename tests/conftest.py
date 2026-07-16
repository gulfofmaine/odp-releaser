from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


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
    """Ensure app credential env vars never leak in from the host."""
    for name in (
        "DISPATCH_APPS",
        "DISPATCH_APP_ID",
        "DISPATCH_APP_PRIVATE_KEY",
        "REPORTER_APPS",
        "REPORTER_APP_ID",
        "REPORTER_APP_PRIVATE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
