import difflib
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Protocol

import ruamel.yaml
import typer

from odp_releaser.github_output import write_github_output, write_step_summary
from odp_releaser.logger import logger
from odp_releaser.manifests.file import update_file_with_payload
from odp_releaser.manifests.helm import update_helm_values_with_payload
from odp_releaser.manifests.kustomize import update_kustomize_with_payload
from odp_releaser.report_metadata import ReportMetadata, embed_metadata
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import ImageConfig, ManifestConfig

DEFAULT_CONFIG_PATH = Path(".github/image_manifest.yaml")


class _HasPath(Protocol):
    path: Path


def _apply_manifest[ManifestT: _HasPath](
    manifest: ManifestT,
    update_fn: Callable[[Path, str, ManifestT, ClientPayload, list[str]], str],
    config_path: Path,
    payload: ClientPayload,
    commit_message: list[str],
    *,
    dry_run: bool,
) -> bool:
    """Resolve, update, diff and (unless ``dry_run``) write a single manifest.

    Returns ``True`` when the update changed the manifest's contents. The
    update's commit message entries are only appended to ``commit_message``
    when the contents actually changed, so unchanged manifests don't show up
    in the audit trail.
    """
    manifest_path = (config_path.parent / manifest.path).resolve()
    try:
        # Keep commit messages and logs readable (and stable across runners)
        # by referring to manifests relative to the working directory.
        display_path = manifest_path.relative_to(Path.cwd())
    except ValueError:
        display_path = manifest_path
    original_manifest = manifest_path.read_text()
    manifest_messages: list[str] = []
    updated_manifest = update_fn(
        display_path, original_manifest, manifest, payload, manifest_messages
    )

    changed = updated_manifest != original_manifest
    if changed:
        commit_message.extend(manifest_messages)

    diff = difflib.unified_diff(
        original_manifest.splitlines(),
        updated_manifest.splitlines(),
        fromfile="original",
        tofile="updated",
        lineterm="",
    )
    logger.info(f"Diff for {manifest_path}:\n{'\n'.join(diff)}")

    if not dry_run:
        manifest_path.write_text(updated_manifest)
        logger.warning(f"Wrote updated manifest for {manifest_path}")
    else:
        logger.warning(f"Dry run, not writing updated manifest for {manifest_path}")

    return changed


def bump_images(
    client_payload: Annotated[
        str,
        typer.Argument(
            envvar="CLIENT_PAYLOAD",
            help="repository_dispatch client_payload string, can be loaded from env: `CLIENT_PAYLOAD`",
        ),
    ],
    *,
    config_path: Annotated[
        Path,
        typer.Option(
            envvar="IMAGE_MANIFEST_CONFIG_PATH",
            help=(
                "Path to the image manifest configuration file. Paths inside "
                "the config (manifest paths) are resolved relative to this "
                "file's parent directory."
            ),
        ),
    ] = DEFAULT_CONFIG_PATH,
    dry_run: Annotated[
        bool, typer.Option(help="Template the changes, but don't write them")
    ] = False,
) -> None:
    """Update the deployment with the given manifest config and client payload."""
    logger.debug(f"Manifest config path is: {config_path}")
    logger.debug(f"Client payload is: {client_payload}")
    logger.info(f"Dry run is: {dry_run}")

    payload = ClientPayload.model_validate_json(client_payload)
    logger.debug("Parsed client payload:")
    logger.debug(payload)

    logger.debug("Raw config:")
    raw_config = config_path.read_text()  # pylint: disable=unspecified-encoding
    logger.debug(raw_config)

    yaml = ruamel.yaml.YAML(typ="safe", pure=True)

    config_yaml = yaml.load(raw_config)

    config = ManifestConfig.model_validate(config_yaml)
    logger.debug("Parsed manifest config:")
    logger.debug(config)

    if (
        config.allowed_source_repos is not None
        and payload.repo not in config.allowed_source_repos
    ):
        message = (
            f"Source repository '{payload.repo}' is not in the "
            "allowed_source_repos configured for this deployment"
        )
        logger.error(message)
        typer.echo(message, err=True)
        raise typer.Exit(1)

    if payload.image_name not in config.images:
        configured_images = (
            ", ".join(sorted(config.images)) if config.images else "(none)"
        )
        message = (
            f"Image '{payload.image_name}' is not configured in {config_path}; "
            f"configured images: {configured_images}"
        )
        logger.error(message)
        typer.echo(message, err=True)
        raise typer.Exit(1)

    commit_message = [
        f"Update image {payload.image_name} to {payload.new_tag()}",
        "",
        f"Triggered by {payload.source.url}",
        "",
    ]

    changed = False
    update_mode = "commit"
    environment: str | None = None
    environment_url: str | None = None

    if image_configs := config.images.get(payload.image_name):
        logger.debug("Configs for the image:")
        logger.debug(image_configs)

        filtered_configs: list[ImageConfig] = [
            image_config
            for image_config in image_configs
            if image_config.events is None
            or payload.source.event in image_config.events
        ]

        logger.debug("Filtered configs")
        logger.debug(filtered_configs)

        update_modes = {image_config.update_mode for image_config in filtered_configs}
        if len(update_modes) > 1:
            logger.warning(
                f"Mixed update_mode values across matching configs for "
                f"{payload.image_name}: {sorted(update_modes)}; using pull_request"
            )
        if "pull_request" in update_modes:
            update_mode = "pull_request"

        environment = _resolve_report_setting(
            filtered_configs, config.environment, "environment", payload.image_name
        )
        environment_url = _resolve_report_setting(
            filtered_configs,
            config.environment_url,
            "environment_url",
            payload.image_name,
        )
        if environment_url is not None:
            environment_url = environment_url.format(**payload.value_format_kwargs())

        for image_config in filtered_configs:
            for kustomize_manifest in image_config.kustomize_manifests:
                if _apply_manifest(
                    kustomize_manifest,
                    update_kustomize_with_payload,
                    config_path,
                    payload,
                    commit_message,
                    dry_run=dry_run,
                ):
                    changed = True
            for helm_manifest in image_config.helm_charts:
                if _apply_manifest(
                    helm_manifest,
                    update_helm_values_with_payload,
                    config_path,
                    payload,
                    commit_message,
                    dry_run=dry_run,
                ):
                    changed = True
            for file_manifest in image_config.file_manifests:
                if _apply_manifest(
                    file_manifest,
                    update_file_with_payload,
                    config_path,
                    payload,
                    commit_message,
                    dry_run=dry_run,
                ):
                    changed = True

    logger.info(f"Commit message: \n{'\n'.join(commit_message)}")

    metadata = ReportMetadata(
        environment=environment,
        environment_url=environment_url,
        client_payload=payload,
    )
    sanitized_image_name = payload.image_name.replace("/", "-")
    pr_title, pr_body = _pr_title_and_body(commit_message, metadata)
    write_github_output(
        {
            "changed": "true" if changed else "false",
            "image_name": payload.image_name,
            "digest": payload.digest,
            "update_mode": update_mode,
            "environment": environment or "",
            "environment_url": environment_url or "",
            "branch_name": f"odp-releaser/bump-{sanitized_image_name}",
            "commit_message": "\n".join(commit_message),
            "pr_title": pr_title,
            "pr_body": pr_body,
        }
    )
    write_step_summary(f"# {'\n'.join(commit_message)}")


def _resolve_report_setting(
    filtered_configs: list[ImageConfig],
    default: str | None,
    attr: str,
    image_name: str,
) -> str | None:
    """Resolve a report setting across the matching configs.

    Each config's own value falls back to the manifest-level ``default``. A
    config that resolves to no value has no opinion; when the configs that do
    have one disagree, warn and use the first in config order (mirroring the
    mixed-``update_mode`` handling).
    """
    values = [
        resolved
        for image_config in filtered_configs
        if (resolved := getattr(image_config, attr) or default) is not None
    ]
    if not values:
        return default
    if len(set(values)) > 1:
        logger.warning(
            f"Mixed {attr} values across matching configs for "
            f"{image_name}: {sorted(set(values))}; using {values[0]!r}"
        )
    return values[0]


def _pr_title_and_body(
    commit_message: list[str], metadata: ReportMetadata
) -> tuple[str, str]:
    """Derive a self-contained PR title/body pair from ``commit_message``.

    ``commit_message`` is always ``[title, "", "Triggered by ...", "", *body]``
    (see the assembly above), so the title is the first line and the body is
    everything after the blank separator line, with a trailing footer added so
    the PR body stands on its own without any workflow-side templating. The
    report ``metadata`` is appended as an invisible HTML comment so a
    merge-time `report-deployment --pr-body` run can finish the deployment
    report without any other context.
    """
    title = commit_message[0]
    body_lines = list(commit_message[2:])
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
    body_lines.extend(
        ["", "Automated image bump by odp-releaser.", "", embed_metadata(metadata)]
    )
    return title, "\n".join(body_lines)


if __name__ == "__main__":
    typer.run(bump_images)
