from __future__ import annotations

from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from odp_releaser.schemas.example_yaml import example_yaml

SET_DESCRIPTION = (
    "Mapping of yamlpath expressions to templated values. "
    "Values may reference `{new_tag}`, `{git_sha}`, `{digest}`, and `{payload}`"
)
# NOTE: ``set`` fields inline ``Field(default_factory=dict)`` rather than share a
# module-level ``Annotated`` alias so the pydantic mypy plugin can see the
# default and not treat the field as required.


class KustomizeManifest(BaseModel):
    """Kustomize manifest configuration. Updates the image overrides and set fields."""

    # ``example_yaml`` renders this model as a bare path string when every field
    # other than ``path`` is left at its default.
    _shorthand_field: ClassVar[str] = "path"

    path: Annotated[
        Path,
        Field(description="Relative path to the Kustomize manifest"),
    ]
    set: dict[str, str] = Field(default_factory=dict, description=SET_DESCRIPTION)
    pin: Annotated[
        Literal["tag", "digest"],
        Field(
            description=(
                "Whether the kustomize images entry pins the tag (newTag) or "
                "the immutable digest (digest)"
            ),
        ),
    ] = "tag"

    @model_validator(mode="before")
    @classmethod
    def coerce_path_string(cls, value: object) -> object:
        if isinstance(value, (str, Path)):
            return {"path": value}
        return value


class HelmManifest(BaseModel):
    """Helm manifest configuration with Dagster user deployments chart layout shorthand."""

    path: Annotated[
        Path,
        Field(description="Relative path to the Helm values file"),
    ]
    dagster_user_code: Annotated[
        bool,
        Field(
            description=(
                "When true, update the image.tag of every entry in the "
                "top-level 'deployments' list whose image.repository matches "
                "the released image (Dagster user-deployments chart layout)"
            ),
        ),
    ] = False
    set: dict[str, str] = Field(default_factory=dict, description=SET_DESCRIPTION)


class FileManifest(BaseModel):
    """A generic YAML or JSON manifest updated purely via ``set`` paths.

    Unlike the kustomize and helm manifests there is no implicit image field
    to update, so a bare-string form carries no useful information; the mapping
    form with an explicit ``set`` is required.
    """

    path: Annotated[
        Path,
        Field(description="Relative path to the file manifest"),
    ]
    set: Annotated[
        dict[str, str],
        Field(description=SET_DESCRIPTION),
    ]


class AllowedActors(BaseModel):
    """Actors allowed to trigger a config's bumps. Both lists empty denies everyone."""

    # Assignment form (not Annotated) so mypy's pydantic plugin sees the
    # default_factory and treats these as optional constructor arguments.
    users: list[str] = Field(
        description=(
            "GitHub usernames, compared case-insensitively against the "
            "payload's source actor"
        ),
        default_factory=list,
    )
    teams: list[str] = Field(
        description=(
            "GitHub teams as org/team-slug entries. Membership is resolved "
            "via the GitHub API, which needs a GITHUB_TOKEN with organization "
            "members read access (an app or PAT token; the default workflow "
            "token cannot read team membership)"
        ),
        default_factory=list,
    )


class ImageConfig(BaseModel):
    """Configuration for an image, specifying which manifests to update and how."""

    events: Annotated[
        list[Literal["push", "publish", "workflow_dispatch", "release"]] | None,
        Field(
            description="List of GitHub events for these manifests. Only these events will trigger updates. If `None`, all events trigger updates.",
        ),
    ] = None
    allowed_source_repos: Annotated[
        list[str] | None,
        Field(
            description=(
                "Full repo names (owner/name) allowed to trigger this "
                "config. Replaces the defaults-level list; unset inherits "
                "it. A config whose resolved list rejects the payload's "
                "repo is skipped"
            ),
        ),
    ] = None
    allowed_actors: Annotated[
        AllowedActors | None,
        Field(
            description=(
                "Users and teams allowed to trigger this config. Replaces "
                "the defaults-level setting; unset inherits it. A config "
                "whose resolved actors reject the payload's actor is "
                "skipped. Use YAML merge keys (<<: *anchor) to share "
                "allowlists between configs"
            ),
        ),
    ] = None
    environment: Annotated[
        str | None,
        Field(
            description=(
                "GitHub environment name reported back to the source repo "
                "for this config's bumps (`report-deployment`). Overrides "
                "the defaults-level environment; unset falls back to the "
                "deploy repo's owner/name slug"
            ),
        ),
    ] = None
    environment_url: Annotated[
        str | None,
        Field(
            description=(
                "URL reported as the deployment's 'View deployment' link, "
                "e.g. where this config's app runs. May reference "
                "`{new_tag}`, `{git_sha}`, and `{digest}`. Overrides the "
                "defaults-level environment_url; unset falls back to the "
                "bump commit or pull request URL"
            ),
        ),
    ] = None
    update_mode: Annotated[
        Literal["commit", "pull_request"],
        Field(
            description=(
                "Whether to commit the change directly or open a pull "
                "request for review"
            ),
        ),
    ] = "commit"
    reviewers: Annotated[
        list[str] | None,
        Field(
            description=(
                "GitHub usernames requested as reviewers when this config's "
                "bump opens a pull request. Replaces the defaults-level "
                "list; unset inherits it, [] requests none"
            ),
        ),
    ] = None
    team_reviewers: Annotated[
        list[str] | None,
        Field(
            description=(
                "GitHub team slugs (no org prefix) requested as reviewers "
                "on the bump pull request. Replaces the defaults-level list"
            ),
        ),
    ] = None
    # Assignment form (not Annotated) so mypy's pydantic plugin sees the
    # default_factory and treats these as optional constructor arguments.
    kustomize_manifests: list[KustomizeManifest] = Field(
        description="List of Kustomize manifests to set for the image",
        default_factory=list,
    )
    helm_charts: list[HelmManifest] = Field(
        description="List of Helm values files to update for the image",
        default_factory=list,
    )
    file_manifests: list[FileManifest] = Field(
        description="List of generic YAML or JSON manifests updated via set paths",
        default_factory=list,
    )


class ConfigDefaults(BaseModel):
    """Default settings for every image config; a config's own value replaces the default."""

    allowed_source_repos: Annotated[
        list[str] | None,
        Field(
            description=(
                "Full repo names (owner/name) allowed to trigger bumps; "
                "None disables the check"
            ),
        ),
    ] = None

    allowed_actors: Annotated[
        AllowedActors | None,
        Field(
            description=(
                "Users and teams allowed to trigger bumps; None disables the check"
            ),
        ),
    ] = None

    environment: Annotated[
        str | None,
        Field(
            description=(
                "Default GitHub environment name used when reporting "
                "deployments back to source repos (`report-deployment`). "
                "Overridable per image config; unset falls back to this "
                "deploy repo's owner/name slug"
            ),
        ),
    ] = None

    environment_url: Annotated[
        str | None,
        Field(
            description=(
                "Default URL reported as the deployment's 'View deployment' "
                "link. May reference `{new_tag}`, `{git_sha}`, and "
                "`{digest}`. Overridable per image config; unset falls back "
                "to the bump commit or pull request URL"
            ),
        ),
    ] = None

    reviewers: Annotated[
        list[str] | None,
        Field(
            description=(
                "GitHub usernames requested as reviewers on bump pull requests"
            ),
        ),
    ] = None

    team_reviewers: Annotated[
        list[str] | None,
        Field(
            description=(
                "GitHub team slugs (no org prefix) requested as reviewers "
                "on bump pull requests. Requesting a team review needs an "
                "app or PAT token; the default workflow token cannot"
            ),
        ),
    ] = None


class ManifestConfig(BaseModel):
    """Configuration for image manifests, mapping image names to their update configurations."""

    # Assignment form (not Annotated) so mypy's pydantic plugin sees the
    # default_factory and treats the field as an optional constructor argument.
    defaults: ConfigDefaults = Field(
        description=(
            "Default settings applied to every image config; a config's "
            "own value replaces the default"
        ),
        default_factory=ConfigDefaults,
    )

    images: Annotated[
        dict[str, list[ImageConfig]],
        Field(description="Mapping of image names to their configurations"),
    ]
    """Mapping of image names to manifests to update"""

    @classmethod
    def generate_yaml(cls) -> str:
        """Render the bundled :data:`EXAMPLE_MANIFEST` as commented YAML."""
        return example_yaml(EXAMPLE_MANIFEST)


EXAMPLE_MANIFEST = ManifestConfig(
    defaults=ConfigDefaults(
        allowed_source_repos=[
            "gulfofmaine/Neracoos-1-Buoy-App",
            "ioos/buoy_retriever",
        ],
        allowed_actors=AllowedActors(
            users=["abkfenris"],
            teams=["gulfofmaine/deployers"],
        ),
        environment="staging",
        environment_url="https://staging.neracoos.org",
        reviewers=["abkfenris"],
        team_reviewers=["deployers"],
    ),
    images={
        "gmri/neracoos-mariners-dashboard": [
            ImageConfig(
                events=["publish"],
                allowed_source_repos=["gulfofmaine/Neracoos-1-Buoy-App"],
                allowed_actors=AllowedActors(users=["abkfenris"]),
                update_mode="pull_request",
                environment="production",
                environment_url="https://mariners.neracoos.org",
                reviewers=["abkfenris"],
                team_reviewers=["mariners"],
                kustomize_manifests=[
                    KustomizeManifest(path=Path("../apps/mariners/kustomization.yaml")),
                ],
                helm_charts=[
                    HelmManifest(
                        path=Path("../apps/sea-eagle/values.yaml"),
                        dagster_user_code=True,
                    ),
                ],
                file_manifests=[
                    FileManifest(
                        path=Path("../apps/config/deployment.json"),
                        set={
                            "/spec/template/spec/containers[0]/image": (
                                "gmri/example@{digest}"
                            )
                        },
                    ),
                ],
            ),
            ImageConfig(
                events=["push"],
                kustomize_manifests=[
                    KustomizeManifest(
                        path=Path("apps/mariners-dev/kustomization.yaml"),
                        pin="digest",
                    ),
                ],
                helm_charts=[
                    HelmManifest(
                        path=Path("../apps/sea-eagle/values.yaml"),
                        dagster_user_code=True,
                    ),
                ],
                file_manifests=[
                    FileManifest(
                        path=Path("../apps/config/deployment.json"),
                        set={
                            "/spec/template/spec/containers[0]/image": (
                                "gmri/example@{digest}"
                            )
                        },
                    ),
                ],
            ),
        ],
    },
)
