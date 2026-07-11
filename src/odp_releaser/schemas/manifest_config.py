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


class ImageConfig(BaseModel):
    """Configuration for an image, specifying which manifests to update and how."""

    events: Annotated[
        list[Literal["push", "publish", "workflow_dispatch", "release"]] | None,
        Field(
            description="List of GitHub events for these manifests. Only these events will trigger updates. If `None`, all events trigger updates.",
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
    kustomize_manifests: Annotated[
        list[KustomizeManifest],
        Field(
            description="List of Kustomize manifests to set for the image",
            default_factory=list,
        ),
    ]
    helm_charts: Annotated[
        list[HelmManifest],
        Field(
            description="List of Helm values files to update for the image",
            default_factory=list,
        ),
    ]
    file_manifests: Annotated[
        list[FileManifest],
        Field(
            description=(
                "List of generic YAML or JSON manifests updated via set paths"
            ),
            default_factory=list,
        ),
    ]


class ManifestConfig(BaseModel):
    """Configuration for image manifests, mapping image names to their update configurations."""

    images: Annotated[
        dict[str, list[ImageConfig]],
        Field(description="Mapping of image names to their configurations"),
    ]
    """Mapping of image names to manifests to update"""

    allowed_source_repos: Annotated[
        list[str] | None,
        Field(
            description=(
                "Full repo names (owner/name) allowed to trigger bumps; "
                "None disables the check"
            ),
        ),
    ] = None

    @classmethod
    def generate_yaml(cls) -> str:
        """Render the bundled :data:`EXAMPLE_MANIFEST` as commented YAML."""
        return example_yaml(EXAMPLE_MANIFEST)


EXAMPLE_MANIFEST = ManifestConfig(
    images={
        "gmri/neracoos-mariners-dashboard": [
            ImageConfig(
                events=["publish"],
                update_mode="pull_request",
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
    allowed_source_repos=[
        "gulfofmaine/Neracoos-1-Buoy-App",
        "ioos/buoy_retriever",
    ],
)
