from typing import Annotated

from pydantic import BaseModel, Field


class DispatchAppCredentials(BaseModel):
    """Credentials for a deploy org's GitHub dispatch app."""

    app_id: Annotated[str, Field(..., description="GitHub App ID of the dispatch app")]
    private_key: Annotated[
        str, Field(..., description="PEM-encoded private key of the app")
    ]


class DeployTarget(BaseModel):
    """A repository that should send ``repository_dispatch`` event."""

    owner: Annotated[str, Field(..., description="Owner of the deploy repository")]
    repo: Annotated[str, Field(..., description="Name of the deploy repository")]
    event_type: Annotated[
        str,
        Field(description="``repository_dispatch`` event type to send"),
    ] = "image-published"


EXAMPLE_TARGETS: list[DeployTarget] = [
    DeployTarget(owner="gulfofmaine", repo="some-deploy-repo"),
    DeployTarget(owner="ioos", repo="other-deploy", event_type="custom-event"),
]
