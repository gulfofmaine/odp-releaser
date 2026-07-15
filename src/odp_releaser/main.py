import importlib.metadata
import logging
from typing import TYPE_CHECKING, cast

import typer
import typer.completion
from typer._completion_classes import completion_init
from typer._completion_shared import Shells

if TYPE_CHECKING:
    from typer import _click

from odp_releaser.bump_image_tester import test_bump_images
from odp_releaser.bump_images import bump_images
from odp_releaser.generate_config import app as generate_app
from odp_releaser.logger import logger
from odp_releaser.make_payload import make_payload
from odp_releaser.notify import notify, test_notify

app = typer.Typer(
    no_args_is_help=True,
    name="odp-releaser",
    help="Tooling to help manage releasing Docker images across repos",
    add_completion=False,
)


app_completion = typer.Typer(
    no_args_is_help=True, help="Generate and install completion scripts.", hidden=True
)
app.add_typer(app_completion, name="completion")


@app_completion.command(
    no_args_is_help=True,
    help="Show completion for the specified shell, to copy or customize it.",
)
def show(ctx: typer.Context, shell: Shells) -> None:
    typer.completion.show_callback(ctx, cast("_click.Parameter", None), shell)


@app_completion.command(
    no_args_is_help=True, help="Install completion for the specified shell."
)
def install(ctx: typer.Context, shell: Shells) -> None:
    typer.completion.install_callback(ctx, cast("_click.Parameter", None), shell)


app.add_typer(generate_app, name="generate-config")
app.command()(notify)
app.command()(bump_images)

app_test = typer.Typer(
    no_args_is_help=True,
    name="test",
)
app_test.command(name="notify")(test_notify)
app_test.command(name="bump-images")(test_bump_images)
app_test.command(name="make-payload")(make_payload)
app.add_typer(app_test, name="test")


@app_test.callback()
def test_main() -> None:
    """Validate configs by mock running `notify` or `bump-images`."""
    # These are interactive validation commands, so default to INFO
    # verbosity; explicit -v/-vv/-vvv on the main app still wins.
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)


def _version_callback(value: bool) -> None:
    """Print version and exit if --version is passed."""
    if value:
        version = importlib.metadata.version("odp-releaser")
        typer.echo(f"odp-releaser {version}")
        raise typer.Exit


# Define an init function, with common options
# -------------------
@app.callback()
def main(
    ctx: typer.Context,
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        min=0,
        max=3,
        help="Increase verbosity of logging",
    ),
    _version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version",
    ),
) -> None:
    """
    ODP Releaser Command Line Interface.
    """

    # Set logging level
    # -------------------
    # 50: Crit
    # 40: Err
    # 30: Warn
    # 20: Info
    # 10: Debug
    # 0: Not set
    verbose = 30 - (verbose * 10)
    verbose = verbose if verbose > 10 else logging.DEBUG
    logger.setLevel(level=verbose)

    ctx.obj = {
        "verbose": verbose,
    }
    completion_init()


if __name__ == "__main__":
    app()
