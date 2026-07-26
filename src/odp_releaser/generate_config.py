import json

import typer

from odp_releaser.bump_images import DEFAULT_CONFIG_PATH
from odp_releaser.schemas.dispatch import EXAMPLE_TARGETS
from odp_releaser.schemas.example_yaml import example_yaml
from odp_releaser.schemas.json_schema import (
    deploy_targets_schema,
    image_manifest_schema,
)
from odp_releaser.schemas.manifest_config import ManifestConfig

app = typer.Typer(
    help="Generate configuration files for ODP Releaser", no_args_is_help=True
)

schema_app = typer.Typer(
    help="Generate JSON Schemas for ODP Releaser configuration files",
    no_args_is_help=True,
)
app.add_typer(schema_app, name="schema")


@app.command()
def image_manifest() -> None:
    """Generate an `image_manifest.yaml` configuration file for deploy repo to call with `odp-releaser bump-images`."""
    typer.secho(f"\n# Default config path: {DEFAULT_CONFIG_PATH}\n#")

    typer.secho(ManifestConfig.generate_yaml())


@app.command()
def deploy_targets() -> None:
    """Generate a `deploy_targets.yaml` configuration for a source repo to call with `odp-releaser notify`."""
    typer.secho(
        "# Default config path ``.github/deploy_targets.yaml`` in source repos to be"
    )
    typer.secho("# parsed by the ``notify`` command.\n")
    typer.secho(example_yaml(EXAMPLE_TARGETS))


@schema_app.command(name="image-manifest")
def schema_image_manifest() -> None:
    """Print the JSON Schema for `image_manifest.yaml` configs.

    Every object in the schema is strict (`additionalProperties: false`),
    catching the same typo'd keys `odp-releaser validate image-manifest`
    does, plus the `x-`-prefixed keys this project's YAML anchor convention
    (`x-guards: &guards`) relies on at the top level.
    """
    typer.echo(json.dumps(image_manifest_schema(), indent=2))


@schema_app.command(name="deploy-targets")
def schema_deploy_targets() -> None:
    """Print the JSON Schema for `deploy_targets.yaml` configs.

    Every object in the schema is strict (`additionalProperties: false`),
    catching the same typo'd keys `odp-releaser validate deploy-targets` does.
    """
    typer.echo(json.dumps(deploy_targets_schema(), indent=2))


if __name__ == "__main__":
    app()
