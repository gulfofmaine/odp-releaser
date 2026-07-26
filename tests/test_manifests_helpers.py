from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import HttpUrl

from odp_releaser.manifests.helpers import (
    apply_set_templates,
    display_manifest_path,
    open_for_editing,
    resolve_manifest_path,
)
from odp_releaser.schemas.client_payload import ClientPayload, ClientPayloadSource


def test_resolve_manifest_path_is_relative_to_configs_own_directory(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "sub" / "image_manifest.yaml"
    resolved = resolve_manifest_path(config_path, Path("../apps/values.yaml"))

    assert resolved == (tmp_path / "apps" / "values.yaml").resolve()


def test_resolve_manifest_path_ignores_the_process_cwd(tmp_path: Path) -> None:
    config_path = tmp_path / "image_manifest.yaml"
    resolved = resolve_manifest_path(config_path, Path("kustomization.yaml"))

    assert resolved == (tmp_path / "kustomization.yaml").resolve()
    assert resolved != (Path.cwd() / "kustomization.yaml").resolve()


def test_display_manifest_path_relativizes_to_cwd_when_possible() -> None:
    manifest_path = (Path.cwd() / "some" / "manifest.yaml").resolve()

    assert display_manifest_path(manifest_path) == Path("some/manifest.yaml")


def test_display_manifest_path_falls_back_to_resolved_path_outside_cwd(
    tmp_path: Path,
) -> None:
    outside = (tmp_path / "elsewhere" / "manifest.yaml").resolve()

    # tmp_path is not under the cwd, so relative_to raises and the resolved
    # path itself is returned unchanged.
    assert display_manifest_path(outside) == outside


def _payload() -> ClientPayload:
    return ClientPayload(
        image_name="gmri/app",
        digest="sha256:" + "0" * 64,
        tag="1.2.3",
        git_sha="0" * 40,
        image_ref="gmri/app@sha256:" + "0" * 64,
        repo="gulfofmaine/odp-releaser",
        source=ClientPayloadSource(
            event="push",
            ref="main",
            url=HttpUrl("https://example.invalid/x"),
            run_url=HttpUrl("https://example.invalid/x"),
            actor="tester",
        ),
    )


def test_apply_set_templates_keyerror_names_the_bad_path_and_value() -> None:
    """A ``KeyError`` from ``str.format`` on an unknown placeholder must be
    re-raised naming the offending ``set`` path and value -- the bare
    ``str.format`` KeyError alone doesn't say which of several ``set``
    entries was the culprit.
    """
    processor = open_for_editing("some:\n  path: old\n")
    commit_message: list[str] = []

    with pytest.raises(KeyError, match=r"/some/path.*\{sha\}"):
        apply_set_templates(
            processor, {"/some/path": "{sha}"}, _payload(), commit_message
        )

    # Nothing should have been recorded for a set that never applied.
    assert commit_message == []
