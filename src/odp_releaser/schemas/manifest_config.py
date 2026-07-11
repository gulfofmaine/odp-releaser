from pathlib import Path
from typing import Annotated, Literal

from goodconf import Field, GoodConf
from pydantic import model_validator

SET_DESCRIPTION = (
    "Mapping of yamlpath expressions to templated values. "
    "Values may reference `{new_tag}`, `{git_sha}`, `{digest}`, and `{payload}`"
)
SET_ANNOTATION = Annotated[
    dict[str, str],
    Field(
        description=SET_DESCRIPTION,
        initial=lambda: {
            "/spec/template/spec/containers[0]/image": ("gmri/example@{digest}")
        },
        default_factory=dict,
    ),
]


class KustomizeManifest(GoodConf):
    """Kustomize manifest configuration. Updates the image overrides and set fields."""

    path: Annotated[
        Path,
        Field(
            initial=lambda: "../apps/mariners/kustomization.yaml",
            description="Relative path to the Kustomize manifest",
        ),
    ]
    set: SET_ANNOTATION
    pin: Annotated[
        Literal["tag", "digest"],
        Field(
            ...,
            description=(
                "Whether the kustomize images entry pins the tag (newTag) or "
                "the immutable digest (digest)"
            ),
            initial=lambda: "tag",
        ),
    ] = "tag"

    @model_validator(mode="before")
    @classmethod
    def coerce_path_string(cls, value: object) -> object:
        if isinstance(value, (str, Path)):
            return {"path": value}
        return value


class HelmManifest(GoodConf):
    """Helm manifest configuration with Dagster user deployments chart layout shorthand."""

    path: Annotated[
        Path,
        Field(
            initial=lambda: "../apps/sea-eagle/values.yaml",
            description="Relative path to the Helm values file",
        ),
    ]
    dagster_user_code: Annotated[
        bool,
        Field(
            description=(
                "When true, update the image.tag of every entry in the "
                "top-level 'deployments' list whose image.repository matches "
                "the released image (Dagster user-deployments chart layout)"
            ),
            initial=lambda: True,
        ),
    ] = False
    set: SET_ANNOTATION


class FileManifest(GoodConf):
    """A generic YAML or JSON manifest updated purely via ``set`` paths.

    Unlike the kustomize and helm manifests there is no implicit image field
    to update, so a bare-string form carries no useful information; the mapping
    form with an explicit ``set`` is required.
    """

    path: Annotated[
        Path,
        Field(
            initial=lambda: "../apps/config/deployment.json",
            description="Relative path to the file manifest",
        ),
    ]
    set: Annotated[
        dict[str, str],
        Field(
            description=SET_DESCRIPTION,
            initial=lambda: {
                "/spec/template/spec/containers[0]/image": ("gmri/example@{digest}")
            },
        ),
    ]


class ImageConfig(GoodConf):
    """Configuration for an image, specifying which manifests to update and how."""

    events: Annotated[
        list[Literal["push", "publish", "workflow_dispatch", "release"]] | None,
        Field(
            ...,
            description="List of GitHub events for these manifests. Only these events will trigger updates. If `None`, all events trigger updates.",
            initial=lambda: ["push", "publish"],
        ),
    ] = None
    update_mode: Annotated[
        Literal["commit", "pull_request"],
        Field(
            ...,
            description=(
                "Whether to commit the change directly or open a pull "
                "request for review"
            ),
            initial=lambda: "commit",
        ),
    ] = "commit"
    # copy_to_ecr: Annotated[
    #     bool,
    #     Field(
    #         ..., description="Whether to copy the image to ECR", initial=lambda: True
    #     ),
    # ] = False
    kustomize_manifests: Annotated[
        list[KustomizeManifest],
        Field(
            description="List of Kustomize manifests to set for the image",
            initial=lambda: ["../apps/mariners/kustomization.yaml"],
            default_factory=list,
        ),
    ]
    helm_charts: Annotated[
        list[HelmManifest],
        Field(
            description="List of Helm values files to update for the image",
            initial=lambda: [
                {"path": "../apps/sea-eagle/values.yaml", "dagster_user_code": True}
            ],
            default_factory=list,
        ),
    ]
    file_manifests: Annotated[
        list[FileManifest],
        Field(
            description=(
                "List of generic YAML or JSON manifests updated via set paths"
            ),
            initial=lambda: [
                {
                    "path": "../apps/config/deployment.json",
                    "set": {
                        "/spec/template/spec/containers[0]/image": (
                            "gmri/example@{digest}"
                        )
                    },
                }
            ],
            default_factory=list,
        ),
    ]
    # allowed_users: Annotated[
    #     list[str] | None,
    #     Field(
    #         ...,
    #         description="Users who are allowed to trigger deployments",
    #         initial=lambda: ["abkfenris", "Dylan-Pugh"],
    #     ),
    # ] = None
    # allowed_teams: Annotated[
    #     list[str] | None,
    #     Field(
    #         ...,
    #         description="Allowed GitHub teams",
    #         initial=lambda: ["gulfofmaine/odp", "ioos/team2"],
    #     ),
    # ] = None


class ManifestConfig(GoodConf):
    """Configuration for image manifests, mapping image names to their update configurations."""

    images: Annotated[
        dict[str, list[ImageConfig]],
        Field(
            description="Mapping of image names to their configurations",
            initial=lambda: {
                "gmri/neracoos-mariners-dashboard": [
                    ImageConfig.get_initial(
                        events=["publish"], update_mode="pull_request"
                    ),
                    ImageConfig.get_initial(
                        events=["push"],
                        kustomize_manifests=[
                            {
                                "path": "apps/mariners-dev/kustomization.yaml",
                                "pin": "digest",
                            }
                        ],
                    ),
                ]
            },
        ),
    ]
    """Mapping of image names to manifests to update"""

    allowed_source_repos: Annotated[
        list[str] | None,
        Field(
            ...,
            description=(
                "Full repo names (owner/name) allowed to trigger bumps; "
                "None disables the check"
            ),
            initial=lambda: [
                "gulfofmaine/Neracoos-1-Buoy-App",
                "ioos/buoy_retriever",
            ],
        ),
    ] = None
