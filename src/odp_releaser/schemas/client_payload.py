from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl


class Release(BaseModel):
    """Release information"""

    tag: Annotated[str, Field(..., description="Tag of the release")]
    name: Annotated[str, Field(..., description="Name of the release")]
    url: Annotated[HttpUrl, Field(..., description="URL of the release")]


class PullRequest(BaseModel):
    """Pull request information"""

    number: Annotated[int, Field(..., description="Number of the pull request")]
    title: Annotated[str, Field(..., description="Title of the pull request")]
    url: Annotated[HttpUrl, Field(..., description="URL of the pull request")]


class ClientPayloadSource(BaseModel):
    """Event source info"""

    event: Annotated[str, Field(..., description="Event name")]
    ref: Annotated[str, Field(..., description="Branch or tag name")]
    url: Annotated[HttpUrl, Field(..., description="Best link back to the source")]
    run_url: Annotated[
        HttpUrl, Field(..., description="URL to the run associated with the source")
    ]
    actor: Annotated[str, Field(..., description="User who triggered the event")]
    release: Annotated[
        Release | None,
        Field(description="Release information associated with the source"),
    ] = None
    pr: Annotated[
        PullRequest | None,
        Field(description="Pull request information associated with the source"),
    ] = None


class ClientPayload(BaseModel):
    """repository_dispatch payload for image updates."""

    image_name: Annotated[str, Field(..., description="Name of the image")]
    digest: Annotated[str, Field(..., description="Digest of the image")]
    tag: Annotated[str, Field(..., description="Tag of the image")]
    git_sha: Annotated[str, Field(..., description="Git SHA of the commit")]
    image_ref: Annotated[str, Field(..., description="Full reference of the image")]
    source: Annotated[
        ClientPayloadSource, Field(description="Source information of the payload")
    ]
    repo: Annotated[str, Field(..., description="Repository")]

    def new_tag(self) -> str:
        if self.source.event == "release":
            return self.source.ref
        return self.tag

    def value_format_kwargs(self) -> dict[str, str]:
        """Keyword arguments for formatting strings with payload values."""
        return {
            "new_tag": self.new_tag(),
            "git_sha": self.git_sha,
            "digest": self.digest,
            "payload": self.model_dump_json(),
        }
