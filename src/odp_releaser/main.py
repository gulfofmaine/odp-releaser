import logging

import typer
from odp_releaser.logger import logger
from odp_releaser.bump_images import bump_images
from odp_releaser.bump_image_tester import bump_images_tester
from odp_releaser.make_payload import make_payload
from odp_releaser.generate_config import generate_config




app = typer.Typer(no_args_is_help=True, name="odp-releaser", help="Tooling to help manage releasing Docker images across repos")

app.command()(bump_images)
app.command()(bump_images_tester)
app.command()(make_payload)
app.command()(generate_config)

# Define an init function, with common options
# -------------------
@app.callback()
def main(
    ctx: typer.Context,
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, min=0, max=3, help="Increase verbosity"),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version",
    ),
):
    """
    ODP Dispatch Command Line Interface.
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

    # Init myapp
    # -------------------
    # if version:
    #     print(app_version)
    #     return

    ctx.obj = {
        "verbose": verbose,
    }

if __name__ == "__main__":
    app()
