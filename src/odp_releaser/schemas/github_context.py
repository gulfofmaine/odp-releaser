from pydantic import BaseModel, Field


class Repository(BaseModel):
    pass

class Event(BaseModel):
    pass


class GitHubContext(BaseModel):
    ref: str = Field(..., description="Branch or tag name")
    sha: str = Field(..., description="Git SHA of the commit")
    repository: str = Field(..., description="Repository name")
    repository_owner: str = Field(..., description="Repository owner")
    actor: str = Field(..., description="User who triggered the event")
    workflow: str = Field(..., description="Workflow name")
    head_ref: str = Field(..., description="Head reference for the pull request or branch")
    base_ref: str = Field(..., description="Base reference for the pull request or branch")
    event_name: str = Field(..., description="Name of the GitHub event")
    ref_name: str = Field(..., description="Name of the reference")
    ref_type: str = Field(..., description="Type of the reference (branch or tag)")
    event: Event = Field(..., description="Event information")
    workflow_ref: str = Field(..., description="Reference for the workflow")
    workflow_sha: str = Field(..., description="SHA for the workflow")
    triggering_actor: str = Field(..., description="User who triggered the workflow")


# class PrMerge()