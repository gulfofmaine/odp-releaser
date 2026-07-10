from __future__ import annotations

import importlib.metadata

import odp_releaser as m


def test_version() -> None:
    assert importlib.metadata.version("odp_releaser") == m.__version__
