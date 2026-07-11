from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class PrMerge(BaseModel):
    """A pull request associated with a pushed commit.

    This is the output of
    ``gh api repos/{repo}/commits/{sha}/pulls --jq '.[0] // empty'``, which
    can be empty when the commit does not belong to any pull request.
    """

    model_config = ConfigDict(extra="ignore")

    number: Annotated[int, Field(description="Number of the pull request")]
    title: Annotated[str, Field(description="Title of the pull request")]
    html_url: Annotated[str, Field(description="URL of the pull request")]


def parse_pr_merge(text: str) -> PrMerge | None:
    """Parse the output of the ``pr-merge`` ``gh api`` call.

    The workflow captures this with ``--jq '.[0] // empty'``, which produces
    an empty (or whitespace-only) string when the commit is not associated
    with any pull request. Returns ``None`` in that case.
    """
    if not text.strip():
        return None
    return PrMerge.model_validate_json(text)


class ReleaseObject(BaseModel):
    """The ``release`` object of a GitHub ``release`` event."""

    model_config = ConfigDict(extra="ignore")

    tag_name: Annotated[str, Field(description="Tag associated with the release")]
    name: Annotated[str | None, Field(description="Name of the release")] = None
    html_url: Annotated[str, Field(description="URL of the release")]


class ReleaseEvent(BaseModel):
    """A GitHub ``release`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    action: Annotated[str, Field(description="Action that triggered the event")]
    release: Annotated[
        ReleaseObject, Field(description="Release associated with the event")
    ]


class HeadCommit(BaseModel):
    """The ``head_commit`` object of a GitHub ``push`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    id: Annotated[str, Field(description="SHA of the head commit")]
    url: Annotated[str, Field(description="URL of the head commit")]


class PushEvent(BaseModel):
    """A GitHub ``push`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    ref: Annotated[str, Field(description="Full ref that was pushed")]
    after: Annotated[str, Field(description="SHA of the commit after the push")]
    head_commit: Annotated[
        HeadCommit | None, Field(description="Head commit of the push, if any")
    ] = None


class WorkflowDispatchEvent(BaseModel):
    """A GitHub ``workflow_dispatch`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    ref: Annotated[str, Field(description="Ref the workflow was dispatched on")]
    inputs: Annotated[
        dict[str, str] | None,
        Field(description="Inputs provided to the workflow dispatch"),
    ] = None


class GitHubContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: Annotated[str, Field(description="Branch or tag name")]
    sha: Annotated[str, Field(description="Git SHA of the commit")]
    repository: Annotated[str, Field(description="Repository name")]
    repository_owner: Annotated[str, Field(description="Repository owner")]
    actor: Annotated[str, Field(description="User who triggered the event")]
    workflow: Annotated[str, Field(description="Workflow name")]
    head_ref: Annotated[
        str, Field(description="Head reference for the pull request or branch")
    ]
    base_ref: Annotated[
        str, Field(description="Base reference for the pull request or branch")
    ]
    event_name: Annotated[str, Field(description="Name of the GitHub event")]
    ref_name: Annotated[str, Field(description="Name of the reference")]
    ref_type: Annotated[str, Field(description="Type of the reference (branch or tag)")]
    workflow_ref: Annotated[str, Field(description="Reference for the workflow")]
    workflow_sha: Annotated[str, Field(description="SHA for the workflow")]
    triggering_actor: Annotated[
        str, Field(description="User who triggered the workflow")
    ]
