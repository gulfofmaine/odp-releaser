from pathlib import Path
from typing import Annotated

from goodconf import GoodConf, Field
from pydantic import BaseModel, model_validator


class KustomizeManifest(GoodConf):
    path: Annotated[Path, Field(initial=lambda: "apps/mariners/kustomization.yaml")]
    set: Annotated[dict[str, str], Field(initial=lambda: {"/resources[.^github.com/gulfofmaine/Neracoos-1-Buoy-App/k8s?ref=]": "github.com/gulfofmaine/Neracoos-1-Buoy-App/k8s?ref={new_tag}"}, default_factory=dict)]

    @model_validator(mode="before")
    @classmethod
    def coerce_path_string(cls, value):
        if isinstance(value, (str, Path)):
            return {"path": value}
        return value


class HelmManifest(GoodConf):
    path: Annotated[Path, Field(initial=lambda: "apps/sea-eagle/values.yaml")]
    dagster_user_code: Annotated[bool, Field(initial=lambda: True)] = False
    set: Annotated[dict[str, str], Field(initial=dict, default_factory=dict)]

    @model_validator(mode="before")
    @classmethod
    def coerce_path_string(cls, value):
        if isinstance(value, (str, Path)):
            return {"path": value}
        return value


class ImageConfig(GoodConf):
    events: Annotated[
        list[str] | None,
        Field(
            ...,
            description="List of Github events for these manifests",
            initial=lambda: ["push", "publish"],
        ),
    ] = None
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
            default_factory=list
        ),
    ]
    helm_values: Annotated[
        list[HelmManifest],
        Field(
            description="List of Helm values files for Dagster user code",
            initial=lambda: ["apps/sea-eagle/values.yaml"],
            default_factory=list
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
    #         description="Allowed Github teams",
    #         initial=lambda: ["gulfofmaine/odp", "ioos/team2"],
    #     ),
    # ] = None
    # allowed_source_repos: Annotated[
    #     list[str] | None,
    #     Field(
    #         ...,
    #         description="Allowed source repositories",
    #         initial=lambda: ["gulfofmaine/Neracoos-1-Buoy-App", "ioos/buoy_retriever"],
    #     ),
    # ] = None


class ManifestConfig(GoodConf):
    images: Annotated[
        dict[str, list[ImageConfig]],
        Field(
            description="Mapping of image names to their configurations",
            initial=lambda: {
                "gmri/neracoos-mariners-dashboard": [
                    ImageConfig.get_initial(events=["publish"]),
                    ImageConfig.get_initial(
                        events=["push"],
                        kustomize_manifests=["apps/mariners-dev/kustomization.yaml"],
                    ),
                ]
            },
        ),
    ]
    """Mapping of image names to manifests to update"""


if __name__ == "__main__":
    print(ManifestConfig.generate_yaml())
