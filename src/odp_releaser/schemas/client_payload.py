from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl


class Release(BaseModel):
    tag: Annotated[str, Field(..., description="Tag of the release")]
    name: Annotated[str, Field(..., description="Name of the release")]
    url: Annotated[HttpUrl, Field(..., description="URL of the release")]


class PullRequest(BaseModel):
    number: Annotated[int, Field(..., description="Number of the pull request")]
    title: Annotated[str, Field(..., description="Title of the pull request")]
    url: Annotated[HttpUrl, Field(..., description="URL of the pull request")]


class ClientPayloadSource(BaseModel):
    event: Annotated[str, Field(..., description="Event name")]
    ref: Annotated[str, Field(..., description="Branch or tag name")]
    url: Annotated[HttpUrl, Field(..., description="Best link back to the source")]
    run_url: Annotated[HttpUrl, Field(..., description="URL to the run associated with the source")]
    actor: Annotated[str, Field(..., description="User who triggered the event")]
    release: Annotated[Release | None, Field(description="Release information associated with the source")] = None
    pr: Annotated[PullRequest | None, Field(description="Pull request information associated with the source")] = None


class ClientPayload(BaseModel):
    image_name: str = Field(..., description="Name of the image")
    digest: str = Field(..., description="Digest of the image")
    tag: str = Field(..., description="Tag of the image")
    git_sha: str = Field(..., description="Git SHA of the commit")
    image_ref: str = Field(..., description="Full reference of the image")
    source: ClientPayloadSource = Field(..., description="Source information of the payload")
    repo: str = Field(..., description="Repository")

    def new_tag(self):
        if self.source.event == "release":
            return self.source.ref
        return self.tag
