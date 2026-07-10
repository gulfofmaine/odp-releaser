from pydantic import BaseModel, ConfigDict, Field


class PrMerge(BaseModel):
    """A pull request associated with a pushed commit.

    This is the output of
    ``gh api repos/{repo}/commits/{sha}/pulls --jq '.[0] // empty'``, which
    can be empty when the commit does not belong to any pull request.
    """

    model_config = ConfigDict(extra="ignore")

    number: int = Field(..., description="Number of the pull request")
    title: str = Field(..., description="Title of the pull request")
    html_url: str = Field(..., description="URL of the pull request")


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

    tag_name: str = Field(..., description="Tag associated with the release")
    name: str | None = Field(None, description="Name of the release")
    html_url: str = Field(..., description="URL of the release")


class ReleaseEvent(BaseModel):
    """A GitHub ``release`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    action: str = Field(..., description="Action that triggered the event")
    release: ReleaseObject = Field(..., description="Release associated with the event")


class HeadCommit(BaseModel):
    """The ``head_commit`` object of a GitHub ``push`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="SHA of the head commit")
    url: str = Field(..., description="URL of the head commit")


class PushEvent(BaseModel):
    """A GitHub ``push`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    ref: str = Field(..., description="Full ref that was pushed")
    after: str = Field(..., description="SHA of the commit after the push")
    head_commit: HeadCommit | None = Field(
        None, description="Head commit of the push, if any"
    )


class WorkflowDispatchEvent(BaseModel):
    """A GitHub ``workflow_dispatch`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    ref: str = Field(..., description="Ref the workflow was dispatched on")
    inputs: dict[str, str] | None = Field(
        None, description="Inputs provided to the workflow dispatch"
    )


class GitHubContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str = Field(..., description="Branch or tag name")
    sha: str = Field(..., description="Git SHA of the commit")
    repository: str = Field(..., description="Repository name")
    repository_owner: str = Field(..., description="Repository owner")
    actor: str = Field(..., description="User who triggered the event")
    workflow: str = Field(..., description="Workflow name")
    head_ref: str = Field(
        ..., description="Head reference for the pull request or branch"
    )
    base_ref: str = Field(
        ..., description="Base reference for the pull request or branch"
    )
    event_name: str = Field(..., description="Name of the GitHub event")
    ref_name: str = Field(..., description="Name of the reference")
    ref_type: str = Field(..., description="Type of the reference (branch or tag)")
    workflow_ref: str = Field(..., description="Reference for the workflow")
    workflow_sha: str = Field(..., description="SHA for the workflow")
    triggering_actor: str = Field(..., description="User who triggered the workflow")
