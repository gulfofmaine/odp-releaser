import re
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, field_validator

_DIGEST_PATTERN = re.compile(r"^[a-z0-9]+(?:[._+-][a-z0-9]+)*:[0-9a-fA-F]{32,}$")


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
    digest: Annotated[
        str,
        Field(
            ...,
            description=(
                "Bare digest of the image, e.g. 'sha256:<hex>' (no repository prefix)"
            ),
        ),
    ]
    tag: Annotated[str, Field(..., description="Tag of the image")]
    git_sha: Annotated[str, Field(..., description="Git SHA of the commit")]
    image_ref: Annotated[str, Field(..., description="Full reference of the image")]
    source: Annotated[
        ClientPayloadSource, Field(description="Source information of the payload")
    ]
    repo: Annotated[str, Field(..., description="Repository")]

    @field_validator("digest", mode="after")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_PATTERN.match(value):
            msg = (
                f"digest must be a bare image digest like 'sha256:<hex>', got "
                f"'{value}'. Strip any repository prefix (docker inspect "
                "RepoDigests output is 'repo@sha256:...' — pass only the part "
                "after '@')."
            )
            raise ValueError(msg)
        return value

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
