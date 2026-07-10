import typer

from odp_releaser.schemas.manifest_config import ManifestConfig

app = typer.Typer()


def generate_config() -> None:
    """Generate an initial image manifest configuration file."""
    typer.secho(ManifestConfig.generate_yaml())


if __name__ == "__main__":
    typer.run(generate_config)
