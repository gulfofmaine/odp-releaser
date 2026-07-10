from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer()


def make_payload(
    # image_name: Annotated[str, typer.Argument(envvar="IMAGE_NAME")],
    # image_tag: Annotated[str, typer.Argument(envvar="IMAGE_TAG")],
    # image_digest: Annotated[str, typer.Argument(envvar="IMAGE_DIGEST")],
    # github_event_name: Annotated[str, typer.Argument(envvar="GITHUB_EVENT_NAME")],
    github_event_path: Annotated[Path, typer.Argument(envvar="GITHUB_EVENT_PATH")],
    # ref name
    # git sha
    # server url
    github_repository: Annotated[str, typer.Argument(envvar="GITHUB_REPOSITORY")],
    # run id
    github_actor: Annotated[str, typer.Argument(envvar="GITHUB_ACTOR")],
    # release tag should be in event
    # release name should be in event
    # release url should be in event
) -> None:
    """Make a client payload for repository_dispatch call for the given image and GitHub context."""
    # typer.echo(f"Image name is: {image_name}")
    # typer.echo(f"Image tag is: {image_tag}")
    # typer.echo(f"Image digest is: {image_digest}")
    # typer.echo(f"GitHub actor is: {github_actor}")
    # typer.echo(f"GitHub event name is: {github_event_name}")
    # typer.echo(f"GitHub event path is: {github_event_path}")
    # typer.echo(f"GitHub repository is: {github_repository}")


if __name__ == "__main__":
    typer.run(make_payload)
