from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from odp_releaser.schemas.example_yaml import example_yaml

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue
    from pydantic_core import CoreSchema

    from odp_releaser.schemas.client_payload import ClientPayload

SET_DESCRIPTION = (
    "Mapping of yamlpath expressions to templated values. "
    "Values may reference `{new_tag}`, `{git_sha}`, `{digest}`, `{payload}`, "
    "and `{deployed_image}` (the config's `deployed_as`, or the payload's "
    "image name when it declares none)"
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

    @classmethod
    # pylint sees BaseModel's hook as taking no arguments, so an override with
    # pydantic's documented signature reads as arguments-differ.
    def __get_pydantic_json_schema__(  # pylint: disable=arguments-differ
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """Also accept the bare-path shorthand ``coerce_path_string`` allows.

        ``model_json_schema()`` only sees the mapping form, so the generated
        schema in ``schemas/`` (used for editor completion and
        ``check-jsonschema``) would flag the documented
        ``- ./kustomization.yaml`` shorthand as an error. Declaring the union
        here keeps the published schema honest about what the model really
        validates.
        """
        return {
            "anyOf": [
                handler(core_schema),
                {
                    "type": "string",
                    "description": (
                        "Relative path to the Kustomize manifest, shorthand "
                        "for a mapping with only `path` set"
                    ),
                },
            ]
        }


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


# The comment bodies `odp-releaser comment` posts back on the source pull
# request when a config doesn't override them.
#
# Two deliberate constraints, both covered by tests in
# tests/test_manifest_config.py:
#
# - Only placeholders that are always populated. `environment_url` can resolve
#   to an empty string at bump time, which would render a dangling markdown
#   link here.
# - ASCII only. These strings reach stdout through `generate-config
#   image-manifest`, and a Windows shell redirecting that to a file encodes it
#   with the locale code page (cp1252), which raises on anything outside it. A
#   user's own override is their own business -- their platform, their choice --
#   but what ships has to survive `generate-config > image_manifest.yaml`
#   everywhere.
DEFAULT_STAGED_TEMPLATE = """\
### `{image_name}` staged for `{environment}`

Tag `{new_tag}` is staged in [`{deploy_repo}`]({bump_url}) and is waiting on \
review before it deploys.

<sub>Bumped by [odp-releaser]({run_url}) from `{git_sha}`.</sub>"""

DEFAULT_DEPLOYED_TEMPLATE = """\
### `{image_name}` deployed to `{environment}`

Tag `{new_tag}` landed in [`{deploy_repo}`]({bump_url}).

<sub>Bumped by [odp-releaser]({run_url}) from `{git_sha}`.</sub>"""


class CommentConfig(BaseModel):
    """Comment posted back on the source pull request when this image is bumped.

    Every field is optional and inherited independently: an unset field takes
    the ``defaults``-level value, then the built-in template. See
    :func:`resolve_comment_config`.
    """

    enabled: Annotated[
        bool | None,
        Field(
            description=(
                "Whether to comment on the source pull request for this "
                "config's bumps. Unset inherits the defaults-level value, "
                "which itself defaults to true"
            ),
        ),
    ] = None
    staged: Annotated[
        str | None,
        Field(
            description=(
                "Comment body posted while a pull_request-mode bump is open "
                "and awaiting review. May reference the comment placeholders "
                "(see the docs); escape literal braces as `{{`/`}}`. Unset "
                "inherits the defaults-level value, then the built-in template"
            ),
        ),
    ] = None
    deployed: Annotated[
        str | None,
        Field(
            description=(
                "Comment body posted once the bump has landed -- immediately "
                "for a commit-mode bump, or when the bump pull request merges. "
                "Unset inherits the defaults-level value, then the built-in "
                "template"
            ),
        ),
    ] = None


class ResolvedComment(BaseModel):
    """A :class:`CommentConfig` with every level of inheritance applied.

    ``bump_images`` resolves this once and hands it to ``odp-releaser
    comment`` (and embeds it in the bump pull request body for the merge-time
    run), so no consumer has to re-apply the inheritance rules or handle an
    unset field.
    """

    enabled: bool
    staged: str
    deployed: str


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
            "GitHub teams as org/team-slug entries. Membership is checked "
            "with the source org's reporter app credentials (REPORTER_APPS / "
            "REPORTER_APP_ID / REPORTER_APP_PRIVATE_KEY), so that app must "
            "also be granted the organization Members: read permission"
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
    comment: Annotated[
        CommentConfig | None,
        Field(
            description=(
                "Comment posted back on the source pull request for this "
                "config's bumps. Each field is inherited from the "
                "defaults-level setting independently, so overriding one "
                "template keeps the other"
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
                "on the bump pull request. Replaces the defaults-level "
                "list. The bump-images workflow detects this key and mints "
                "its app token with organization Members: read"
            ),
        ),
    ] = None
    deployed_as: Annotated[
        str | None,
        Field(
            description=(
                "The image name the manifests under this config actually "
                "deploy from, when it differs from the payload's "
                "image_name -- e.g. an ECR pull-through cache path such as "
                "'705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/"
                "gmri/sea-eagle-brown-3crs' mirroring upstream "
                "'gmri/sea-eagle-brown-3crs'. Used as the Helm dagster "
                "shorthand's image.repository selector and in `set` "
                "templating via `{deployed_image}`; kustomize's own "
                "`newName` already carries the mirror, so this does not "
                "change which `images:` entry kustomize matches. Per-config only"
            ),
        ),
    ] = None
    sync: Annotated[
        bool | None,
        Field(
            description=(
                "Whether odp-releaser copies the payload's image to "
                "deployed_as before the bump lands. Unset or false means "
                "declare-only: the correct setting for ECR pull-through and "
                "any other registry-native replication, where there is "
                "nothing to copy because the cache populates itself on the "
                "next pull. Overrides the defaults-level value; unset "
                "inherits it, then falls back to false"
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

    comment: Annotated[
        CommentConfig | None,
        Field(
            description=(
                "Default comment posted back on source pull requests when "
                "images are bumped. Overridable per image config, field by "
                "field; unset falls back to the built-in templates"
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
                "on bump pull requests. The bump-images workflow detects "
                "this key and mints its app token with organization "
                "Members: read (the ci app must be granted the permission)"
            ),
        ),
    ] = None

    sync: Annotated[
        bool | None,
        Field(
            description=(
                "Default for whether odp-releaser copies the payload's "
                "image to a config's deployed_as before the bump lands. "
                "Overridable per image config; unset falls back to false."
            ),
        ),
    ] = None


def resolve_setting[SettingT](
    config_value: SettingT | None, default: SettingT | None
) -> SettingT | None:
    """A config's own value, falling back to the defaults-level value.

    Only an unset (``None``) config value inherits the default — an explicit
    empty value (``[]``, ``""``) replaces it. Shared by ``bump_images`` (to
    resolve the settings it actually applies) and the config validator (to
    resolve the same settings when checking configs for agreement), so the
    two can't drift
    on what "resolved" means. It lives here rather than in ``bump_images``
    so the validator can import it without importing ``bump_images`` itself
    (which imports the validator in a later step).
    """
    return config_value if config_value is not None else default


def resolve_comment_config(
    config_value: CommentConfig | None, default: CommentConfig | None
) -> ResolvedComment:
    """A config's comment settings, resolved field by field.

    Unlike every other setting, comment settings are *not* resolved with
    :func:`resolve_setting`. That function replaces a default wholesale, which
    for a nested model means a config overriding only ``deployed`` would
    silently discard a ``defaults``-level ``staged`` template -- the two
    templates describe different lifecycle states and are meant to be set
    independently. So each field falls back on its own: config, then
    ``defaults``, then the built-in.

    Within a single field the inheritance rule is still
    :func:`resolve_setting`'s: only an unset (``None``) value inherits, so an
    explicit ``staged: ""`` is a deliberate "post nothing while staged".
    """
    return ResolvedComment(
        enabled=_resolve_comment_field(config_value, default, "enabled", True),
        staged=_resolve_comment_field(
            config_value, default, "staged", DEFAULT_STAGED_TEMPLATE
        ),
        deployed=_resolve_comment_field(
            config_value, default, "deployed", DEFAULT_DEPLOYED_TEMPLATE
        ),
    )


def _resolve_comment_field[FieldT](
    config_value: CommentConfig | None,
    default: CommentConfig | None,
    attr: str,
    builtin: FieldT,
) -> FieldT:
    """One :class:`CommentConfig` field resolved across all three levels."""
    for source in (config_value, default):
        if source is not None:
            value: FieldT | None = getattr(source, attr)
            if value is not None:
                return value
    return builtin


def deployed_name_for(deployed_as: str | None, upstream: str) -> str:
    """``deployed_as`` when set, otherwise ``upstream``.

    The one place this fallback is spelled: :func:`effective_deployed_name`
    calls it with the payload's ``image_name`` as ``upstream``, and the
    config validator calls it directly with the config's own ``images:`` key
    when no real payload is available to predict the same name from.
    """
    return deployed_as or upstream


def effective_deployed_name(image_config: ImageConfig, payload: ClientPayload) -> str:
    """The image name ``image_config``'s manifests actually deploy from.

    ``deployed_name_for(image_config.deployed_as, payload.image_name)`` --
    the single place that rule is computed with a real payload. Shared by
    ``bump_images`` (which applies this to every manifest engine call), the
    config validator (which needs to predict the identical name a real bump
    would use), and any CLI output that reports the deployed name -- the same
    reason ``resolve_setting`` and :func:`config_matches_event` live here
    rather than in ``bump_images`` itself: it lets every consumer import this
    rule without importing ``bump_images``, which imports the validator in a
    later step, so a hand-respelled copy anywhere else could silently drift
    from what a real bump actually uses.

    Deliberately resolved *per config*, never across configs: unlike every
    other per-config setting ``bump_images`` resolves, this is never routed
    through its ``_resolve_config_setting`` helper, whose "first wins, log a
    warning" behaviour is reasonable for a disagreement over, say, an
    environment name, but would be actively wrong here -- silently deploying
    from one of two configs' declared registries just because it came first
    in the list.
    """
    return deployed_name_for(image_config.deployed_as, payload.image_name)


def config_matches_event(image_config: ImageConfig, event: str) -> bool:
    """Whether ``image_config`` applies to ``event``.

    An ``events: None`` config matches every event; otherwise ``event`` must
    be explicitly listed. This is the exact filter ``bump_images`` applies to
    an image's configs, before resolving any setting or applying any
    manifest, so the validator must use this same predicate rather than
    re-spell it -- otherwise a future change to the event-matching rule could
    land on one side only, leaving the validator checking a different set of
    configs than the ones a real run would apply.
    """
    return image_config.events is None or event in image_config.events


def configs_for_event(
    image_configs: Iterable[ImageConfig], event: str
) -> list[ImageConfig]:
    """The configs in ``image_configs`` that match ``event``, in config order."""
    return [
        image_config
        for image_config in image_configs
        if config_matches_event(image_config, event)
    ]


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
        # Short single-line overrides: the multi-line built-in templates would
        # round-trip through this example as quoted scalars with escaped
        # newlines, which reads badly in the generated docs.
        comment=CommentConfig(
            deployed="`{image_name}` `{new_tag}` deployed to `{environment}`",
        ),
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
                # This deploy repo runs on an ECR pull-through cache mirror of
                # the payload's image, not the upstream name itself. `sync` is
                # left unset (false): a pull-through cache populates itself
                # the first time something pulls the mirrored path, so there
                # is nothing for odp-releaser to copy.
                deployed_as=(
                    "705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/"
                    "gmri/neracoos-mariners-dashboard"
                ),
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
                # Overrides only `deployed`, so the defaults-level value for
                # every other comment field still applies -- comment settings
                # are inherited field by field. `staged` is left to the built-in
                # here because it would never be reached: this config commits
                # directly, and a direct commit is reported as deployed at once.
                comment=CommentConfig(
                    deployed="`{image_name}` `{new_tag}` is live on dev",
                ),
                # Unlike the production config above, this registry doesn't
                # replicate on its own, so `sync` is set: odp-releaser copies
                # the payload's image to `deployed_as` itself before bumping
                # the manifests below to point at it.
                deployed_as="ghcr.io/gulfofmaine/neracoos-mariners-dashboard-dev",
                sync=True,
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
