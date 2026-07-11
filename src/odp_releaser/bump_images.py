import difflib
from pathlib import Path
from typing import Annotated

import ruamel.yaml
import typer

from odp_releaser.github_output import write_github_output
from odp_releaser.logger import logger
from odp_releaser.manifests.kustomize import update_kustomize_with_payload
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import ImageConfig, ManifestConfig

DEFAULT_CONFIG_PATH = Path(".github/image-manifest.yaml")


def bump_images(
    config_path: Annotated[
        Path,
        typer.Argument(
            envvar="IMAGE_MANIFEST_CONFIG_PATH",
            help=(
                "Path to the image manifest configuration file. Paths inside "
                "the config (manifest paths) are resolved relative to this "
                "file's parent directory. Can be loaded from env: "
                "`IMAGE_MANIFEST_CONFIG_PATH`"
            ),
        ),
    ] = DEFAULT_CONFIG_PATH,
    *,
    client_payload: Annotated[
        str,
        typer.Argument(
            envvar="CLIENT_PAYLOAD",
            help="repository_dispatch client_payload string, can be loaded from env: `CLIENT_PAYLOAD`",
        ),
    ],
    dry_run: Annotated[
        bool, typer.Option(help="Template the changes, but don't write them")
    ] = False,
) -> None:
    """Update the deployment with the given manifest config and client payload."""
    logger.debug(f"Manifest config path is: {config_path}")
    logger.debug(f"Client payload is: {client_payload}")
    logger.info(f"Dry run is: {dry_run}")

    payload = ClientPayload.model_validate_json(client_payload)
    # print(f"Parsed manifest config: {config}")
    logger.debug("Parsed client payload:")
    logger.debug(payload)

    logger.debug("Raw config:")
    raw_config = config_path.read_text()
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

    commit_message = [
        f"Update image {payload.image_name} to {payload.new_tag()}",
        "",
        f"Triggered by {payload.source.url}",
        "",
    ]

    changed = False
    update_mode = "commit"

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

        for image_config in filtered_configs:
            for manifest in image_config.kustomize_manifests:
                kustomize_path = (config_path.parent / manifest.path).resolve()
                original_manifest = kustomize_path.read_text()
                updated_manifest = update_kustomize_with_payload(
                    kustomize_path,
                    original_manifest,
                    manifest,
                    payload,
                    commit_message,
                )
                if updated_manifest != original_manifest:
                    changed = True
                diff = difflib.unified_diff(
                    original_manifest.splitlines(),
                    updated_manifest.splitlines(),
                    fromfile="original",
                    tofile="updated",
                    lineterm="",
                )
                diff_text = "\n".join(diff)
                logger.info(f"Diff for {kustomize_path}:\n{diff_text}")
                if not dry_run:
                    kustomize_path.write_text(updated_manifest)
                    logger.warning(f"Wrote updated manifest for {kustomize_path}")
                else:
                    logger.warning(
                        f"Dry run, not writing updated manifest for {kustomize_path}"
                    )

    logger.info(f"Commit message: \n{'\n'.join(commit_message)}")

    sanitized_image_name = payload.image_name.replace("/", "-")
    write_github_output(
        {
            "changed": "true" if changed else "false",
            "update_mode": update_mode,
            "branch_name": f"odp-releaser/bump-{sanitized_image_name}",
            "commit_message": "\n".join(commit_message),
        }
    )


if __name__ == "__main__":
    typer.run(bump_images)
