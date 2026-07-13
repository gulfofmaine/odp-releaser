import typer

from odp_releaser.bump_images import DEFAULT_CONFIG_PATH
from odp_releaser.schemas.dispatch import EXAMPLE_TARGETS
from odp_releaser.schemas.example_yaml import example_yaml
from odp_releaser.schemas.manifest_config import ManifestConfig

app = typer.Typer(
    help="Generate configuration files for ODP Releaser", no_args_is_help=True
)


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


if __name__ == "__main__":
    app()
