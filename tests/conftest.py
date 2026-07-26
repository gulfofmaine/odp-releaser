from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

if TYPE_CHECKING:
    from pathlib import Path


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


@pytest.fixture(autouse=True)
def _default_github_output_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Point ``GITHUB_OUTPUT``/``GITHUB_STEP_SUMMARY`` at per-test temp files by default.

    When the suite runs inside GitHub Actions (e.g. the ``checks`` job in
    ``.github/workflows/ci.yml``), both env vars are already set to the real
    job's output/summary files. A test that exercises
    ``odp_releaser.github_output`` without setting its own value would
    otherwise append to those real files, polluting the actual job's output.
    Setting fixture-default values here -- before the test body runs -- makes
    every test safe by default while still letting a test that calls
    ``monkeypatch.setenv("GITHUB_OUTPUT", ...)`` itself win, since that call
    happens later (during the test body) on the same ``monkeypatch`` object.
    """
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github_output"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "github_step_summary"))
