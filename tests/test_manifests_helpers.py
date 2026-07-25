from __future__ import annotations

from pathlib import Path

from odp_releaser.manifests.helpers import display_manifest_path, resolve_manifest_path


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
