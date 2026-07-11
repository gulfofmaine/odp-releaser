"""Shared Typer option type aliases for GitHub Actions environment inputs.

Typer 0.26.8 cannot group several ``typer.Option``s into one parameter via a
dataclass, so the repeated ``Annotated`` definitions used by more than one
command live here as module-level type aliases. Commands import these and use
them directly in their signatures so option names, envvars, help text, and
defaults stay identical across commands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

GitHubEventName = Annotated[
    str, typer.Option(envvar="GITHUB_EVENT_NAME", help="Name of the GitHub event")
]
GitHubEventPath = Annotated[
    Path,
    typer.Option(envvar="GITHUB_EVENT_PATH", help="Path to the event payload JSON"),
]
GitHubRepository = Annotated[
    str,
    typer.Option(envvar="GITHUB_REPOSITORY", help="`owner/name` of the repo"),
]
GitHubActor = Annotated[
    str, typer.Option(envvar="GITHUB_ACTOR", help="User who triggered the event")
]
GitHubRunId = Annotated[
    str, typer.Option(envvar="GITHUB_RUN_ID", help="ID of the workflow run")
]
GitHubRefName = Annotated[
    str, typer.Option(envvar="GITHUB_REF_NAME", help="Name of the ref")
]
GitHubSha = Annotated[
    str, typer.Option(envvar="GITHUB_SHA", help="Git SHA of the commit")
]
ImageRepository = Annotated[
    str | None,
    typer.Option(
        envvar="IMAGE_REPOSITORY",
        help="Registry/namespace the image was pushed to. Defaults to "
        "ghcr.io/ + the owner of GITHUB_REPOSITORY.",
    ),
]
GitHubToken = Annotated[
    str | None,
    typer.Option(
        envvar="GITHUB_TOKEN",
        help="Token used to look up the pull request associated with a "
        "pushed commit. Without it, push events are dispatched with a "
        "null pr.",
    ),
]
GitHubServerUrl = Annotated[
    str,
    typer.Option(envvar="GITHUB_SERVER_URL", help="Base URL of the GitHub server"),
]
