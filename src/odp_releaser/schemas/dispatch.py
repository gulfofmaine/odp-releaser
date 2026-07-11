from pydantic import BaseModel, Field


class DispatchAppCredentials(BaseModel):
    """Credentials for a deploy org's GitHub dispatch app."""

    app_id: str = Field(..., description="GitHub App ID of the dispatch app")
    private_key: str = Field(..., description="PEM-encoded private key of the app")


class DeployTarget(BaseModel):
    """A repository that should receive a ``repository_dispatch`` event.

    Parsed from ``.github/deploy_targets.yaml`` by the ``notify`` command.
    """

    owner: str = Field(..., description="Owner of the deploy repository")
    repo: str = Field(..., description="Name of the deploy repository")
    event_type: str = Field(
        "image-published",
        description="``repository_dispatch`` event type to send",
    )
