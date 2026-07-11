from pathlib import Path
from typing import Annotated, Literal

from goodconf import Field, GoodConf
from pydantic import model_validator


class KustomizeManifest(GoodConf):
    path: Annotated[Path, Field(initial=lambda: "apps/mariners/kustomization.yaml")]
    set: Annotated[
        dict[str, str],
        Field(
            initial=lambda: {
                "/resources[.^github.com/gulfofmaine/Neracoos-1-Buoy-App/k8s?ref=]": "github.com/gulfofmaine/Neracoos-1-Buoy-App/k8s?ref={new_tag}"
            },
            default_factory=dict,
        ),
    ]
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
    path: Annotated[Path, Field(initial=lambda: "apps/sea-eagle/values.yaml")]
    dagster_user_code: Annotated[bool, Field(initial=lambda: True)] = False
    set: Annotated[dict[str, str], Field(initial=dict, default_factory=dict)]

    @model_validator(mode="before")
    @classmethod
    def coerce_path_string(cls, value: object) -> object:
        if isinstance(value, (str, Path)):
            return {"path": value}
        return value


class ImageConfig(GoodConf):
    events: Annotated[
        list[str] | None,
        Field(
            ...,
            description="List of GitHub events for these manifests",
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
            initial=lambda: ["apps/mariners/kustomization.yaml"],
            default_factory=list,
        ),
    ]
    helm_values: Annotated[
        list[HelmManifest],
        Field(
            description="List of Helm values files for Dagster user code",
            initial=lambda: ["apps/sea-eagle/values.yaml"],
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
