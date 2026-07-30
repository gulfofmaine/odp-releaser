"""Semantic validation of an ``image_manifest.yaml`` config, beyond its schema.

:mod:`odp_releaser.validation.unknown_keys` catches typo'd keys pydantic
silently ignores; pydantic itself catches keys with the wrong shape. Neither
catches a config that is *shaped* correctly but *means* something that
``bump_images`` (and the manifest engines it calls: :mod:`manifests.kustomize`,
:mod:`manifests.helm`, :mod:`manifests.file`) will mishandle at bump time --
often minutes into a release, in CI, against a real payload. Every check in
this module exists because of a specific failure mode observed in that
runtime path: a ``{sha}`` typo that should have been ``{git_sha}`` (a
``KeyError`` deep in ``str.format``), a manifest path that doesn't exist (an
``OSError`` while trying to read it), a ``yamlpath`` selector that never
matches (``mustexist=True`` raising), a lopsided ``allowed_actors.teams``
entry that hard-exits the whole run, and so on. Running these checks ahead of
time -- in CI on the config repo, or as a ``bump-images --dry-run`` pre-flight
-- turns a mid-release failure into a fast, readable diagnostic.

The engines (``update_kustomize_with_payload``, ``update_helm_values_with_payload``,
``update_file_with_payload``) are pure ``(path, text, manifest, payload,
deployed_name, commit_message) -> str`` functions with no filesystem I/O of their own
(``bump_images._apply_manifest`` does all the reading and writing) -- so
they're the actual source of truth for "would this manifest apply cleanly",
not a prediction of it. Every hand-rolled check below exists *only* for
message quality (naming the offending selector/placeholder and listing valid
alternatives): once a manifest's checks report no error, :mod:`.engine_backstop`
runs the real engine against the manifest's actual text and turns any
exception into a diagnostic, catching whatever failure mode the hand-rolled
checks don't (yet) model without needing to be kept in sync by hand.

``validate_image_manifest`` is the file-level entry point: it reads and
parses the file, schema-validates it, and then walks every ``images:`` entry.
``validate_image_configs`` does the semantic checks for one image's configs
and is exported separately because a later step teaches ``bump_images``
itself to call it with the exact configs it is about to apply (the configs
already selected by event and authorization) -- at that point there's no raw
YAML to re-read, so this function works from already-validated
:class:`~odp_releaser.schemas.manifest_config.ImageConfig` models alone, never
touching a line number it wasn't handed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from yamlpath import Processor, YAMLPath
from yamlpath.exceptions import YAMLPathException

from odp_releaser.comment_body import COMMENT_TEMPLATE_KEYS, synthesize_context
from odp_releaser.manifests.file import update_file_with_payload
from odp_releaser.manifests.helm import (
    dagster_deployment_path,
    update_helm_values_with_payload,
)
from odp_releaser.manifests.helpers import (
    display_manifest_path,
    open_for_editing,
    resolve_manifest_path,
)
from odp_releaser.manifests.kustomize import (
    image_entry_path,
    image_pin_path,
    update_kustomize_with_payload,
)
from odp_releaser.schemas.manifest_config import (
    AllowedActors,
    CommentConfig,
    ConfigDefaults,
    FileManifest,
    HelmManifest,
    ImageConfig,
    KustomizeManifest,
    ManifestConfig,
    config_matches_event,
    effective_deployed_name,
)
from odp_releaser.validation.cross_config import check_cross_config
from odp_releaser.validation.deployed_as import (
    check_deployed_as_and_sync,
    check_file_manifest_set_upstream_name,
    check_kustomize_deployed_as_agreement,
)
from odp_releaser.validation.diagnostics import Diagnostics
from odp_releaser.validation.engine_backstop import (
    representative_event,
    run_engine_backstop,
)
from odp_releaser.validation.image_name_shape import image_name_shape_problems
from odp_releaser.validation.manifest_text import load_manifest_text
from odp_releaser.validation.ruamel_lines import line_for_index, line_for_key
from odp_releaser.validation.unknown_keys import report_unknown_keys

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from odp_releaser.schemas.client_payload import ClientPayload

# The placeholders every ``set``/``environment_url`` template may reference.
# Four of these are drawn straight from :meth:`ClientPayload.value_format_kwargs`;
# ``deployed_image`` is not one of them -- ``apply_set_templates`` adds it
# itself (see its docstring), formatted from ``effective_deployed_name``
# rather than read off the payload -- so it's listed here alongside the
# payload-derived keys but excluded from the cross-check below. Kept here
# (rather than re-derived) and cross-checked by a test against a real
# payload's ``value_format_kwargs()`` keys plus ``deployed_image``, so the
# two can't silently drift if a placeholder is ever added or renamed on
# either side.
TEMPLATE_KEYS: frozenset[str] = frozenset(
    {"new_tag", "git_sha", "digest", "payload", "deployed_image"}
)


@dataclass(frozen=True)
class ConfigLocation:
    """Dotted config path plus the best-known source line for diagnostics.

    ``validate_image_configs`` works from already-validated models, which
    carry no source line info at all -- only a caller sitting on the raw
    YAML (``validate_image_manifest``) can look one up. Rather than thread a
    raw line number through every helper here, callers build one
    ``ConfigLocation`` per image (or config item) with the best line they
    could cheaply find, and every diagnostic about something nested under it
    -- a ``set`` selector, a manifest path -- inherits that same "best-known"
    line via :meth:`child`, since nothing more precise is available once
    we're working with models alone.
    """

    location: str
    line: int | None = None

    def child(self, suffix: str, *, line: int | None = None) -> ConfigLocation:
        """A location for something nested under this one.

        A ``suffix`` starting with ``[`` (a list index) is appended
        directly (``<loc>[0]``); anything else is dot-joined
        (``<loc>.kustomize_manifests``), mirroring the convention
        ``unknown_keys`` already uses for dotted config paths. ``line``
        defaults to this location's own line, since a caller building a
        child location usually has no better line than its parent's.
        """
        if suffix.startswith("["):
            new_location = f"{self.location}{suffix}"
        elif self.location:
            new_location = f"{self.location}.{suffix}"
        else:
            new_location = suffix
        return ConfigLocation(new_location, line if line is not None else self.line)


def validate_image_manifest(
    config_path: Path,
    *,
    payload: ClientPayload | None = None,
    check_files: bool = True,
) -> Diagnostics:
    """Read, parse, schema-check, and semantically validate one config file.

    Mirrors the order ``bump_images`` itself effectively relies on: a file
    that can't be read or parsed is fatal (one diagnostic, nothing else to
    check); unknown keys are reported but don't stop schema validation (they
    would already have been silently dropped by pydantic); a schema failure
    stops before the semantic checks below, since those need valid models to
    call attributes on. Only once all of that passes does each ``images:``
    entry get checked, restricted to ``payload.image_name`` and its matching
    events when a ``payload`` is given (the same filter ``bump_images``
    itself applies before doing anything).
    """
    diagnostics = Diagnostics(config_path)

    try:
        raw_config = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError subclasses ValueError, not OSError, so a config
        # that isn't valid UTF-8 escaped an OSError-only guard as a traceback.
        diagnostics.error(f"Could not read {config_path}: {exc}")
        return diagnostics

    yaml = YAML()
    try:
        data = yaml.load(raw_config)
    except YAMLError as exc:
        diagnostics.error(f"Could not parse as YAML: {exc}", line=_yaml_error_line(exc))
        return diagnostics

    report_unknown_keys(ManifestConfig, data, diagnostics, allowed_key_prefixes=("x-",))

    try:
        config = ManifestConfig.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors():
            error_location = ".".join(str(part) for part in error["loc"])
            diagnostics.error(
                error["msg"],
                location=error_location or None,
                line=_line_for_loc(data, error["loc"]),
            )
        return diagnostics

    images_data = data.get("images") if isinstance(data, Mapping) else None

    defaults_location = ConfigLocation(
        "defaults",
        line=line_for_key(data, "defaults") if isinstance(data, Mapping) else None,
    )
    _check_repo_and_actor_shape(
        config.defaults.allowed_source_repos,
        config.defaults.allowed_actors,
        defaults_location,
        diagnostics,
    )
    _check_team_reviewers_shape(
        config.defaults.team_reviewers, defaults_location, diagnostics
    )
    _validate_comment_templates(config.defaults.comment, defaults_location, diagnostics)
    if config.defaults.environment_url is not None:
        _validate_template_value(
            config.defaults.environment_url,
            defaults_location.child("environment_url"),
            diagnostics,
            payload,
            warn_if_no_placeholder=False,
        )

    for image_name, image_configs in config.images.items():
        image_line = line_for_key(images_data, image_name)
        _check_image_name(image_name, diagnostics, line=image_line)

        if payload is not None and image_name != payload.image_name:
            continue

        configs_data = (
            images_data.get(image_name) if isinstance(images_data, Mapping) else None
        )
        matching = [
            (index, image_config)
            for index, image_config in enumerate(image_configs)
            if payload is None
            or config_matches_event(image_config, payload.source.event)
        ]
        if not matching:
            continue

        first_index = matching[0][0]
        item_line = line_for_index(configs_data, first_index)
        location = ConfigLocation(
            f'images."{image_name}"',
            line=item_line if item_line is not None else image_line,
        )

        validate_image_configs(
            config_path,
            image_name,
            [image_config for _, image_config in matching],
            config.defaults,
            payload=payload,
            check_files=check_files,
            diagnostics=diagnostics,
            location=location,
        )

    return diagnostics


def validate_image_configs(
    config_path: Path,
    image_name: str,
    image_configs: Sequence[ImageConfig],
    defaults: ConfigDefaults,
    *,
    payload: ClientPayload | None = None,
    check_files: bool = True,
    diagnostics: Diagnostics | None = None,
    location: ConfigLocation | None = None,
) -> Diagnostics:
    """Semantic checks for one image's configs, given already-validated models.

    Called once per image (whether from :func:`validate_image_manifest`, one
    call per ``images:`` entry, or later directly from ``bump_images`` with
    the exact configs it is about to apply) so that the cross-config checks
    (duplicate manifest targets, disagreeing settings) can see every config
    for the image at once -- they warn about
    *disagreements* and *collisions* between configs, which is meaningless
    looking at one config in isolation. Each referenced manifest file is
    loaded at most once and cached by resolved path, since several configs
    commonly point at the same ``values.yaml``.
    """
    if diagnostics is None:
        diagnostics = Diagnostics(config_path)
    base = (
        location if location is not None else ConfigLocation(f'images."{image_name}"')
    )

    cache: dict[Path, str | None] = {}

    for index, image_config in enumerate(image_configs):
        item_location = base.child(f"[{index}]")
        _validate_config_item(
            config_path,
            image_name,
            image_config,
            defaults,
            item_location,
            diagnostics,
            cache,
            payload=payload,
            check_files=check_files,
        )

    check_cross_config(
        config_path, image_name, image_configs, defaults, base, diagnostics
    )

    return diagnostics


# --- File-level helpers -----------------------------------------------------


def _yaml_error_line(exc: YAMLError) -> int | None:
    """Best-effort 1-based line for a ruamel ``YAMLError``, when it has a mark.

    Prefers ``context_mark`` -- where the construct that never got closed
    actually *opened* (e.g. the ``{`` of an unterminated flow mapping) --
    over ``problem_mark``, which for this class of error sits wherever the
    parser gave up looking (often the end of the stream, well past anything
    a reader could act on). Falls back to ``problem_mark`` when there's no
    context mark at all, which is the common case for simpler errors that
    only ever have one relevant location.
    """
    mark = getattr(exc, "context_mark", None) or getattr(exc, "problem_mark", None)
    line = getattr(mark, "line", None)
    return line + 1 if isinstance(line, int) else None


def _line_for_loc(data: object, loc: tuple[int | str, ...]) -> int | None:
    """Deepest known source line along a pydantic error's ``loc`` path.

    A ``ValidationError``'s ``.errors()`` carry only a dotted/indexed
    ``loc`` (e.g. ``("images", "gmri/two", 0, "update_mode")``), never a
    line -- unlike the semantic checks elsewhere in this module, which
    already have a :class:`ConfigLocation` to hand a line from. This walks
    ``loc`` through the raw ruamel-loaded ``data`` (never the validated
    model, which carries no ``lc`` info), reusing ``line_for_key``/
    ``line_for_index`` -- the same helpers every other line lookup in this
    module goes through -- rather than re-deriving ``.lc.data`` indexing
    here. Each step remembers the best (deepest) line found so far and only
    descends into ``node``'s child when that key/index actually exists
    there; the walk stops (falling back to whatever line was already found,
    or ``None``) the moment a step doesn't resolve, since a ``loc`` entry
    can reference something pydantic synthesized (e.g. a discriminated
    union arm) that has no corresponding raw node.
    """
    node = data
    line: int | None = None
    for part in loc:
        if isinstance(part, str) and isinstance(node, Mapping) and part in node:
            found = line_for_key(node, part)
            node = node[part]
        elif isinstance(part, int) and isinstance(node, list) and 0 <= part < len(node):
            found = line_for_index(node, part)
            node = node[part]
        else:
            break
        if found is not None:
            line = found
    return line


def _check_image_name(
    image_name: str, diagnostics: Diagnostics, *, line: int | None
) -> None:
    """The ``images:`` key must be a name a real payload could equal.

    The shape rules themselves live in :func:`image_name_shape_problems`,
    shared with the identical check on ``ImageConfig.deployed_as`` (see
    :mod:`odp_releaser.validation.deployed_as`) so the two can't drift --
    a config keyed on (or declaring a mirror as) anything other than a
    real image name can never equal ``payload.image_name`` (or a real
    ``image.repository``/``newName``) and so can never match or agree with
    anything.
    """
    problems = image_name_shape_problems(image_name)
    if not problems:
        return
    message = (
        f"images key {image_name!r} {'; '.join(problems)}: it must equal the "
        "payload's image_name exactly, or this config can never match a bump"
    )
    diagnostics.error(message, line=line, location=f'images."{image_name}"')


def _check_repo_and_actor_shape(
    allowed_source_repos: list[str] | None,
    allowed_actors: AllowedActors | None,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Shape-check ``allowed_source_repos`` and ``allowed_actors``.

    Shared between ``defaults`` (checked once, in :func:`validate_image_manifest`)
    and each config's own values (checked once per config, in
    :func:`_validate_config_item`) since both flow through the same
    ``bump_images._config_authorizes``/``_TeamMembershipChecker`` at runtime.
    """
    if allowed_source_repos is not None:
        if not allowed_source_repos:
            diagnostics.warning(
                "allowed_source_repos is explicitly empty ([]); this denies "
                "every source repository",
                location=location.child("allowed_source_repos").location,
                line=location.line,
            )
        for repo in allowed_source_repos:
            if repo.count("/") != 1 or not all(repo.split("/")):
                diagnostics.error(
                    f"allowed_source_repos entry {repo!r} must be an "
                    "'owner/name' pair; a bare name can never equal the "
                    "payload's repo, so this entry can never match",
                    location=location.child("allowed_source_repos").location,
                    line=location.line,
                )

    if allowed_actors is None:
        return
    if not allowed_actors.users and not allowed_actors.teams:
        diagnostics.warning(
            "allowed_actors is present but both users and teams are empty; "
            "this denies every actor",
            location=location.child("allowed_actors").location,
            line=location.line,
        )
    for team in allowed_actors.teams:
        org, _, slug = team.partition("/")
        if team.count("/") != 1 or not org or not slug:
            diagnostics.error(
                f"allowed_actors team entry {team!r} must be an "
                "'org/team-slug' pair; actor_in_team hard-exits the run "
                "otherwise",
                location=location.child("allowed_actors.teams").location,
                line=location.line,
            )


def _check_team_reviewers_shape(
    team_reviewers: list[str] | None,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """``team_reviewers`` entries are bare slugs, not ``org/slug`` pairs.

    The inverse shape of the ``allowed_actors.teams`` check, and easy to
    mix up since both are "team" fields -- but ``team_reviewers`` is passed
    straight to GitHub's "request review from teams" API as a bare slug.
    """
    if not team_reviewers:
        return
    for slug in team_reviewers:
        if "/" in slug:
            diagnostics.error(
                f"team_reviewers entry {slug!r} must be a bare team slug "
                "with no org prefix (e.g. 'deployers', not 'org/deployers')",
                location=location.child("team_reviewers").location,
                line=location.line,
            )


# --- Per-config-item checks --------------------------------------------------


def _validate_config_item(
    config_path: Path,
    image_name: str,
    image_config: ImageConfig,
    defaults: ConfigDefaults,
    location: ConfigLocation,
    diagnostics: Diagnostics,
    cache: dict[Path, str | None],
    *,
    payload: ClientPayload | None,
    check_files: bool,
) -> None:
    if not (
        image_config.kustomize_manifests
        or image_config.helm_charts
        or image_config.file_manifests
    ):
        diagnostics.warning(
            "no kustomize_manifests, helm_charts, or file_manifests are "
            "configured; this config is a silent no-op",
            location=location.location,
            line=location.line,
        )

    check_deployed_as_and_sync(
        image_name, image_config, defaults, location, diagnostics
    )

    _check_repo_and_actor_shape(
        image_config.allowed_source_repos,
        image_config.allowed_actors,
        location,
        diagnostics,
    )
    _check_team_reviewers_shape(image_config.team_reviewers, location, diagnostics)
    _validate_comment_templates(image_config.comment, location, diagnostics)

    if image_config.environment_url is not None:
        _validate_template_value(
            image_config.environment_url,
            location.child("environment_url"),
            diagnostics,
            payload,
            warn_if_no_placeholder=False,
        )

    event = representative_event(image_config)
    # The same rule bump_images applies at bump time (see
    # effective_deployed_name's docstring), just evaluated against `image_name`
    # instead of `payload.image_name` when no real payload is available --
    # the two are guaranteed equal whenever `payload` is not None, since a
    # caller only ever reaches this config with a payload that already
    # matched `image_name` (see validate_image_manifest's own filtering).
    deployed_name = (
        effective_deployed_name(image_config, payload)
        if payload is not None
        else (image_config.deployed_as or image_name)
    )

    for index, kustomize_manifest in enumerate(image_config.kustomize_manifests):
        _validate_kustomize(
            config_path,
            image_name,
            kustomize_manifest,
            location.child(f"kustomize_manifests[{index}]"),
            diagnostics,
            cache,
            payload=payload,
            deployed_name=deployed_name,
            deployed_as=image_config.deployed_as,
            event=event,
            check_files=check_files,
        )

    for index, helm_manifest in enumerate(image_config.helm_charts):
        _validate_helm(
            config_path,
            image_name,
            helm_manifest,
            location.child(f"helm_charts[{index}]"),
            diagnostics,
            cache,
            payload=payload,
            deployed_name=deployed_name,
            event=event,
            check_files=check_files,
        )

    for index, file_manifest in enumerate(image_config.file_manifests):
        _validate_file_manifest(
            config_path,
            image_name,
            file_manifest,
            location.child(f"file_manifests[{index}]"),
            diagnostics,
            cache,
            payload=payload,
            deployed_name=deployed_name,
            deployed_as=image_config.deployed_as,
            event=event,
            check_files=check_files,
        )


def _node_exists(processor: Processor, selector: str) -> bool:
    """Whether ``selector`` resolves to at least one node, without ever auto-vivifying it.

    Always ``mustexist=True``: with ``mustexist=False``, yamlpath builds the
    missing path into the cached, shared ``processor.data`` as a side
    effect of merely checking, which would corrupt every later check against
    the same cached file. A ``YAMLPathException`` (raised only once the
    generator is iterated, hence the ``list(...)``) means "does not exist".
    """
    try:
        return bool(list(processor.get_nodes(selector, mustexist=True)))
    except YAMLPathException:
        return False


def _get_node(processor: Processor, selector: str) -> Any:
    """The first node ``selector`` resolves to, or ``None`` if it doesn't exist."""
    try:
        nodes = list(processor.get_nodes(selector, mustexist=True))
    except YAMLPathException:
        return None
    return nodes[0].node if nodes else None


def _validate_kustomize(
    config_path: Path,
    image_name: str,
    manifest: KustomizeManifest,
    location: ConfigLocation,
    diagnostics: Diagnostics,
    cache: dict[Path, str | None],
    *,
    payload: ClientPayload | None,
    deployed_name: str,
    deployed_as: str | None,
    event: str,
    check_files: bool,
) -> None:
    error_count = len(diagnostics.errors)
    text = load_manifest_text(
        cache,
        diagnostics,
        config_path,
        manifest.path,
        location,
        check_files=check_files,
    )
    processor = open_for_editing(text) if text is not None else None
    _validate_set(
        manifest.set,
        location.child("set"),
        diagnostics,
        processor,
        payload,
        deployed_name,
    )

    # image_selector is deliberately still built from image_name (the
    # upstream name), not deployed_name -- update_kustomize_with_payload
    # matches the same `/images[name=...]` entry regardless of deployed_as,
    # since kustomize's own `newName` carries the mirror. See
    # update_kustomize_with_payload's docstring for why.
    if processor is not None:
        image_selector = image_entry_path(image_name)
        if manifest.pin == "tag":
            # update_kustomize_with_payload writes exactly this path when pin
            # is "tag", with mustexist=True -- so a missing node here is
            # fatal at bump time.
            tag_selector = image_pin_path(image_name, "tag")
            if not _node_exists(processor, tag_selector):
                diagnostics.error(
                    f"{tag_selector} does not exist, but pin is 'tag' and "
                    "bump-images sets it with mustexist=True",
                    location=location.child("pin").location,
                    line=location.line,
                )
        elif not _node_exists(processor, image_selector):
            # pin is "digest": the engine writes image_pin_path(image_name,
            # "digest") with mustexist=False, which can create the missing
            # "digest" leaf but not a missing "images[name=...]" entry itself
            # -- so what actually blocks the write is the *entry*, checked
            # here.
            diagnostics.warning(
                f"no {image_selector} entry exists yet; pin is 'digest', "
                "and bump-images sets the digest with mustexist=False there, "
                "which won't create a missing images entry",
                location=location.child("pin").location,
                line=location.line,
            )

        images_node = _get_node(processor, image_selector)
        if (
            isinstance(images_node, Mapping)
            and "newTag" in images_node
            and "digest" in images_node
        ):
            diagnostics.warning(
                f"{image_selector} has both newTag and digest set; "
                "kustomize prefers digest over newTag, so bumping the tag "
                "has no visible effect",
                location=location.location,
                line=location.line,
            )

        check_kustomize_deployed_as_agreement(
            deployed_as, images_node, location, diagnostics
        )

    if text is not None and len(diagnostics.errors) == error_count:
        resolved = resolve_manifest_path(config_path, manifest.path)
        run_engine_backstop(
            display_manifest_path(resolved),
            text,
            manifest,
            update_kustomize_with_payload,
            image_name,
            event,
            payload,
            deployed_name,
            location,
            diagnostics,
        )


def _validate_helm(
    config_path: Path,
    image_name: str,
    manifest: HelmManifest,
    location: ConfigLocation,
    diagnostics: Diagnostics,
    cache: dict[Path, str | None],
    *,
    payload: ClientPayload | None,
    deployed_name: str,
    event: str,
    check_files: bool,
) -> None:
    error_count = len(diagnostics.errors)
    text = load_manifest_text(
        cache,
        diagnostics,
        config_path,
        manifest.path,
        location,
        check_files=check_files,
    )
    processor = open_for_editing(text) if text is not None else None
    _validate_set(
        manifest.set,
        location.child("set"),
        diagnostics,
        processor,
        payload,
        deployed_name,
    )

    if manifest.dagster_user_code and processor is not None:
        # bump-images matches (and writes) dagster_tag_path against
        # deployed_name, not image_name -- update_helm_values_with_payload
        # matches image.repository, which *is* the deployed name, unlike
        # kustomize's images[name=...] entry (see that engine's docstring
        # for the asymmetry). This only needs to know a deployment entry
        # exists at all, which is dagster_deployment_path (the same prefix
        # that tag path is built from).
        selector = dagster_deployment_path(deployed_name)
        if not _node_exists(processor, selector):
            diagnostics.error(
                f"dagster_user_code is true but no {selector} entry exists; "
                f"this matched on the deployed name {deployed_name!r} "
                "(image_config.deployed_as if set, else the payload's "
                "image_name -- see effective_deployed_name), and "
                "bump-images sets its tag with mustexist=True, which raises",
                location=location.child("dagster_user_code").location,
                line=location.line,
            )

    if text is not None and len(diagnostics.errors) == error_count:
        resolved = resolve_manifest_path(config_path, manifest.path)
        run_engine_backstop(
            display_manifest_path(resolved),
            text,
            manifest,
            update_helm_values_with_payload,
            image_name,
            event,
            payload,
            deployed_name,
            location,
            diagnostics,
        )


def _validate_file_manifest(
    config_path: Path,
    image_name: str,
    manifest: FileManifest,
    location: ConfigLocation,
    diagnostics: Diagnostics,
    cache: dict[Path, str | None],
    *,
    payload: ClientPayload | None,
    deployed_name: str,
    deployed_as: str | None,
    event: str,
    check_files: bool,
) -> None:
    error_count = len(diagnostics.errors)
    text = load_manifest_text(
        cache,
        diagnostics,
        config_path,
        manifest.path,
        location,
        check_files=check_files,
    )
    processor = open_for_editing(text) if text is not None else None
    _validate_set(
        manifest.set,
        location.child("set"),
        diagnostics,
        processor,
        payload,
        deployed_name,
    )

    upstream_name = payload.image_name if payload is not None else image_name
    check_file_manifest_set_upstream_name(
        manifest.set, deployed_as, upstream_name, location.child("set"), diagnostics
    )

    if text is not None and len(diagnostics.errors) == error_count:
        resolved = resolve_manifest_path(config_path, manifest.path)
        run_engine_backstop(
            display_manifest_path(resolved),
            text,
            manifest,
            update_file_with_payload,
            image_name,
            event,
            payload,
            deployed_name,
            location,
            diagnostics,
        )


def _validate_set(
    set_paths: dict[str, str],
    location: ConfigLocation,
    diagnostics: Diagnostics,
    processor: Processor | None,
    payload: ClientPayload | None,
    deployed_name: str,
) -> None:
    """Check every ``set`` selector/value pair for one manifest.

    ``deployed_name`` is folded into the real-``.format()`` check the same
    way ``apply_set_templates`` folds it in at bump time -- as
    ``{"deployed_image": deployed_name}`` alongside ``payload``'s own
    keyword arguments -- so a ``set`` value that legitimately uses
    ``{deployed_image}`` isn't reported as failing to format merely because
    this check built a narrower kwargs dict than the real engine will.
    """
    format_kwargs = (
        payload.value_format_kwargs() | {"deployed_image": deployed_name}
        if payload is not None
        else None
    )
    for selector, value in set_paths.items():
        selector_location = location.child(f'"{selector}"')
        _validate_selector(selector, selector_location, diagnostics, processor)
        _validate_template_value(
            value,
            selector_location,
            diagnostics,
            payload,
            format_kwargs=format_kwargs,
        )


def _validate_selector(
    selector: str,
    location: ConfigLocation,
    diagnostics: Diagnostics,
    processor: Processor | None,
) -> None:
    """A ``set`` selector must parse, and (if the file loaded) must resolve.

    ``YAMLPath`` parses lazily -- constructing it never raises, only
    accessing ``.escaped`` does -- so parseability has to be forced rather
    than just constructed and discarded.
    """
    try:
        path = YAMLPath(selector)
        _ = path.escaped
    except YAMLPathException as exc:
        diagnostics.error(
            f"{selector!r} is not a valid yamlpath: {exc}",
            location=location.location,
            line=location.line,
        )
        return

    if processor is None:
        return
    try:
        list(processor.get_nodes(selector, mustexist=True))
    except YAMLPathException as exc:
        diagnostics.error(
            f"{selector!r} does not resolve in its target manifest: {exc}",
            location=location.location,
            line=location.line,
        )


def _validate_comment_templates(
    comment: CommentConfig | None,
    location: ConfigLocation,
    diagnostics: Diagnostics,
) -> None:
    """Both comment templates only use known, ``.format``-safe placeholders.

    Comment templates have their own vocabulary (see
    :mod:`odp_releaser.comment_body`) and are checked against a synthetic
    context rather than the payload, since most of what a comment can reference
    -- the deploy repo, the bump URL, the resolved environment -- isn't known
    until the bump runs. ``warn_if_no_placeholder`` is off because a wholly
    static comment is a legitimate thing to want.
    """
    if comment is None:
        return
    context_kwargs = synthesize_context().format_kwargs()
    for field in ("staged", "deployed"):
        template: str | None = getattr(comment, field)
        if not template:
            continue
        _validate_template_value(
            template,
            location.child(f"comment.{field}"),
            diagnostics,
            None,
            warn_if_no_placeholder=False,
            valid_keys=COMMENT_TEMPLATE_KEYS,
            format_kwargs=context_kwargs,
        )


def _validate_template_value(
    value: str,
    location: ConfigLocation,
    diagnostics: Diagnostics,
    payload: ClientPayload | None,
    *,
    warn_if_no_placeholder: bool = True,
    valid_keys: frozenset[str] = TEMPLATE_KEYS,
    format_kwargs: dict[str, str] | None = None,
) -> None:
    """A templated value only uses known, ``.format``-safe placeholders.

    ``apply_set_templates`` calls ``value.format(**payload.value_format_kwargs())``
    at bump time, so every failure mode of that call is checked here ahead of
    time: an unknown field name (``KeyError``), a positional field (the
    runtime only ever passes keyword arguments, so ``{0}``/``{}`` also raise
    ``KeyError``), attribute/index access (``{payload.foo}`` -- technically
    ``str.format`` would attempt it, but nothing in ``value_format_kwargs()``
    supports it usefully, so it's flagged rather than silently misbehaving),
    and a stray ``{``/``}`` (``ValueError`` from ``Formatter().parse``
    itself). When real format arguments are available and none of those static
    checks already flagged the value, the actual ``value.format(**kwargs)``
    call is attempted too, so the check is faithful to a real ``bump-images``
    run rather than only to the placeholder names -- but a value already
    reported above isn't reported a second time for failing the very call that
    was predicted to fail.

    ``valid_keys``/``format_kwargs`` default to the ``set``/
    ``environment_url`` vocabulary (:data:`TEMPLATE_KEYS` and the payload's
    own ``value_format_kwargs()``). Comment templates pass their own pair --
    :data:`~odp_releaser.comment_body.COMMENT_TEMPLATE_KEYS` and a synthetic
    context -- because a comment may reference deploy-side run facts that have
    no business being templated into a manifest.
    """
    reported = False
    try:
        parsed = list(Formatter().parse(value))
    except ValueError as exc:
        diagnostics.error(
            f"{value!r} is not a valid format string: {exc}",
            location=location.location,
            line=location.line,
        )
        return

    field_names = [
        field_name for _, field_name, _, _ in parsed if field_name is not None
    ]

    for field_name in field_names:
        if field_name == "" or field_name.isdigit():
            diagnostics.error(
                f"{value!r} uses a positional placeholder "
                f"'{{{field_name}}}'; bump-images calls "
                "str.format(**kwargs), so only named placeholders work",
                location=location.location,
                line=location.line,
            )
        elif "." in field_name or "[" in field_name:
            diagnostics.error(
                f"{value!r} uses attribute/index access "
                f"'{{{field_name}}}', which is not supported; only bare "
                "placeholder names are",
                location=location.location,
                line=location.line,
            )
        elif field_name not in valid_keys:
            known = ", ".join(sorted(valid_keys))
            diagnostics.error(
                f"{value!r} references unknown placeholder "
                f"'{{{field_name}}}'; valid placeholders are: {known}",
                location=location.location,
                line=location.line,
            )
        else:
            continue
        reported = True

    if warn_if_no_placeholder and not field_names:
        diagnostics.warning(
            f"{value!r} has no template placeholder, so bump-images can "
            "never change it",
            location=location.location,
            line=location.line,
        )

    if format_kwargs is None and payload is not None:
        format_kwargs = payload.value_format_kwargs()
    if format_kwargs is not None and not reported:
        try:
            value.format(**format_kwargs)
        except (KeyError, ValueError, IndexError) as exc:
            diagnostics.error(
                f"{value!r} failed to format with the real payload: {exc}",
                location=location.location,
                line=location.line,
            )
