import difflib
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn, Protocol

import ruamel.yaml
import typer
from githubkit.exception import RequestFailed

from odp_releaser.github import (
    AppNotInstalledOnOrgError,
    MissingCredentialsError,
    is_team_member,
    org_installation_token_for,
    resolve_reporter_credentials,
)
from odp_releaser.github_output import write_github_output, write_step_summary
from odp_releaser.logger import logger
from odp_releaser.manifests.file import update_file_with_payload
from odp_releaser.manifests.helm import update_helm_values_with_payload
from odp_releaser.manifests.helpers import (
    display_manifest_path,
    read_manifest_text,
    resolve_manifest_path,
    write_manifest_text,
)
from odp_releaser.manifests.kustomize import update_kustomize_with_payload
from odp_releaser.report_metadata import ReportMetadata, embed_metadata
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import (
    ConfigDefaults,
    ImageConfig,
    ManifestConfig,
    configs_for_event,
    effective_deployed_name,
    resolve_comment_config,
    resolve_setting,
)
from odp_releaser.validation.image_manifest import validate_image_configs

DEFAULT_CONFIG_PATH = Path(".github/image_manifest.yaml")


class _HasPath(Protocol):
    path: Path


def _apply_manifest[ManifestT: _HasPath](
    manifest: ManifestT,
    update_fn: Callable[[Path, str, ManifestT, ClientPayload, str, list[str]], str],
    config_path: Path,
    payload: ClientPayload,
    deployed_name: str,
    commit_message: list[str],
    *,
    dry_run: bool,
) -> bool:
    """Resolve, update, diff and (unless ``dry_run``) write a single manifest.

    Returns ``True`` when the update changed the manifest's contents. The
    update's commit message entries are only appended to ``commit_message``
    when the contents actually changed, so unchanged manifests don't show up
    in the audit trail. ``deployed_name`` (see :func:`effective_deployed_name`)
    is passed straight through to ``update_fn``, which each engine uses
    differently -- see their own docstrings.
    """
    manifest_path = resolve_manifest_path(config_path, manifest.path)
    display_path = display_manifest_path(manifest_path)
    original_manifest, newline = read_manifest_text(manifest_path)
    manifest_messages: list[str] = []
    updated_manifest = update_fn(
        display_path,
        original_manifest,
        manifest,
        payload,
        deployed_name,
        manifest_messages,
    )

    changed = updated_manifest != original_manifest
    if changed:
        commit_message.extend(manifest_messages)

    diff = difflib.unified_diff(
        original_manifest.splitlines(),
        updated_manifest.splitlines(),
        fromfile="original",
        tofile="updated",
        lineterm="",
    )
    logger.info(f"Diff for {manifest_path}:\n{'\n'.join(diff)}")

    if not dry_run:
        write_manifest_text(manifest_path, updated_manifest, newline)
        logger.warning(f"Wrote updated manifest for {manifest_path}")
    else:
        logger.warning(f"Dry run, not writing updated manifest for {manifest_path}")

    return changed


def bump_images(
    client_payload: Annotated[
        str,
        typer.Argument(
            envvar="CLIENT_PAYLOAD",
            help="repository_dispatch client_payload string, can be loaded from env: `CLIENT_PAYLOAD`",
        ),
    ],
    *,
    config_path: Annotated[
        Path,
        typer.Option(
            envvar="IMAGE_MANIFEST_CONFIG_PATH",
            help=(
                "Path to the image manifest configuration file. Paths inside "
                "the config (manifest paths) are resolved relative to this "
                "file's parent directory."
            ),
        ),
    ] = DEFAULT_CONFIG_PATH,
    dry_run: Annotated[
        bool, typer.Option(help="Template the changes, but don't write them")
    ] = False,
) -> None:
    """Update the deployment with the given manifest config and client payload."""
    logger.debug(f"Manifest config path is: {config_path}")
    logger.debug(f"Client payload is: {client_payload}")
    logger.info(f"Dry run is: {dry_run}")

    payload = ClientPayload.model_validate_json(client_payload)
    logger.debug("Parsed client payload:")
    logger.debug(payload)

    logger.debug("Raw config:")
    raw_config = config_path.read_text(encoding="utf-8")
    logger.debug(raw_config)

    yaml = ruamel.yaml.YAML(typ="safe", pure=True)

    config_yaml = yaml.load(raw_config)

    config = ManifestConfig.model_validate(config_yaml)
    logger.debug("Parsed manifest config:")
    logger.debug(config)

    if payload.image_name not in config.images:
        configured_images = (
            ", ".join(sorted(config.images)) if config.images else "(none)"
        )
        message = (
            f"Image '{payload.image_name}' is not configured in {config_path}; "
            f"configured images: {configured_images}"
        )
        logger.error(message)
        typer.echo(message, err=True)
        raise typer.Exit(1)

    commit_message = [
        f"Update image {payload.image_name} to {payload.new_tag()}",
        "",
        f"Triggered by {payload.source.url}",
        "",
    ]

    changed = False
    update_mode = "commit"
    environment: str | None = None
    environment_url: str | None = None
    reviewers: list[str] = []
    team_reviewers: list[str] = []
    sync_destinations: list[str] = []
    comment = resolve_comment_config(None, config.defaults.comment)

    if image_configs := config.images.get(payload.image_name):
        logger.debug("Configs for the image:")
        logger.debug(image_configs)

        filtered_configs: list[ImageConfig] = configs_for_event(
            image_configs, payload.source.event
        )

        logger.debug("Filtered configs")
        logger.debug(filtered_configs)

        team_checker = _TeamMembershipChecker()
        authorized_configs = [
            image_config
            for image_config in filtered_configs
            if _config_authorizes(image_config, config.defaults, payload, team_checker)
        ]
        if filtered_configs and not authorized_configs:
            message = (
                f"No configs for image '{payload.image_name}' allow actor "
                f"'{payload.source.actor}' from repository '{payload.repo}'"
            )
            logger.error(message)
            typer.echo(message, err=True)
            raise typer.Exit(1)

        _preflight(config_path, payload, authorized_configs, config.defaults)

        update_modes = {image_config.update_mode for image_config in authorized_configs}
        if len(update_modes) > 1:
            logger.warning(
                f"Mixed update_mode values across matching configs for "
                f"{payload.image_name}: {sorted(update_modes)}; using pull_request"
            )
        if "pull_request" in update_modes:
            update_mode = "pull_request"

        environment = _resolve_config_setting(
            authorized_configs,
            config.defaults.environment,
            "environment",
            payload.image_name,
        )
        environment_url = _resolve_config_setting(
            authorized_configs,
            config.defaults.environment_url,
            "environment_url",
            payload.image_name,
        )
        if environment_url is not None:
            environment_url = environment_url.format(**payload.value_format_kwargs())

        reviewers = (
            _resolve_config_setting(
                authorized_configs,
                config.defaults.reviewers,
                "reviewers",
                payload.image_name,
            )
            or []
        )
        team_reviewers = (
            _resolve_config_setting(
                authorized_configs,
                config.defaults.team_reviewers,
                "team_reviewers",
                payload.image_name,
            )
            or []
        )
        comment = resolve_comment_config(
            _resolve_config_setting(
                authorized_configs,
                config.defaults.comment,
                "comment",
                payload.image_name,
            ),
            config.defaults.comment,
        )
        sync_destinations = _resolve_sync_destinations(
            authorized_configs, config.defaults, payload.new_tag()
        )

        for image_config in authorized_configs:
            deployed_name = effective_deployed_name(image_config, payload)
            for kustomize_manifest in image_config.kustomize_manifests:
                if _apply_manifest(
                    kustomize_manifest,
                    update_kustomize_with_payload,
                    config_path,
                    payload,
                    deployed_name,
                    commit_message,
                    dry_run=dry_run,
                ):
                    changed = True
            for helm_manifest in image_config.helm_charts:
                if _apply_manifest(
                    helm_manifest,
                    update_helm_values_with_payload,
                    config_path,
                    payload,
                    deployed_name,
                    commit_message,
                    dry_run=dry_run,
                ):
                    changed = True
            for file_manifest in image_config.file_manifests:
                if _apply_manifest(
                    file_manifest,
                    update_file_with_payload,
                    config_path,
                    payload,
                    deployed_name,
                    commit_message,
                    dry_run=dry_run,
                ):
                    changed = True

    logger.info(f"Commit message: \n{'\n'.join(commit_message)}")

    # The source pull request is only known for events that carry one (push,
    # via `pr_for_commit`); a release or workflow_dispatch dispatch has nothing
    # to comment on, and the workflow skips the comment step on the empty value.
    comment_pr_number = payload.source.pr.number if payload.source.pr else None
    metadata = ReportMetadata(
        environment=environment,
        environment_url=environment_url,
        client_payload=payload,
        comment=comment,
        comment_pr_number=comment_pr_number,
    )
    sanitized_image_name = payload.image_name.replace("/", "-")
    pr_title, pr_body = _pr_title_and_body(commit_message, metadata)
    # `sync_source_ref` is only emitted when something will actually sync
    # (i.e. `sync_destinations` is non-empty), matching that output's own
    # "empty means nothing to gate on" contract rather than always populating
    # it: a source ref with no destination is nothing a workflow step, or a
    # human reading the logs, would have a use for.
    sync_source_ref = (
        f"{payload.image_name}@{payload.digest}" if sync_destinations else ""
    )
    write_github_output(
        {
            "changed": "true" if changed else "false",
            "image_name": payload.image_name,
            "digest": payload.digest,
            "update_mode": update_mode,
            "environment": environment or "",
            "environment_url": environment_url or "",
            "branch_name": f"odp-releaser/bump-{sanitized_image_name}",
            "commit_message": "\n".join(commit_message),
            "pr_title": pr_title,
            "pr_body": pr_body,
            "reviewers": ",".join(reviewers),
            "team_reviewers": ",".join(team_reviewers),
            # Templates go out unrendered: the bump pull request's URL only
            # exists after the create-pull-request step runs, so `odp-releaser
            # comment` does the rendering with that URL in hand.
            "comment_enabled": "true" if comment.enabled else "false",
            "comment_pr_number": str(comment_pr_number) if comment_pr_number else "",
            "comment_staged_template": comment.staged,
            "comment_deployed_template": comment.deployed,
            # By digest, not tag: the copy must be of the exact artifact this
            # run just published, not whatever the tag happens to point at by
            # the time a later workflow step runs it.
            "sync_source_ref": sync_source_ref,
            # Newline-delimited `<deployed_as>:<new_tag>` entries, one per
            # distinct sync-enabled authorized config -- see
            # `_resolve_sync_destinations` for why every declared destination
            # gets copied to rather than just the first.
            "sync_destinations": "\n".join(sync_destinations),
        }
    )
    summary_lines = [f"# {'\n'.join(commit_message)}"]
    if sync_destinations:
        summary_lines.append("")
        # Future tense, deliberately. This runs before the action's sync step,
        # so the copy has not happened yet -- and this process cannot know
        # whether it ever will: `--dry-run` and the action's `sync: false`
        # input both skip that step, and neither is visible from here. Past
        # tense here claimed a copy that never happened.
        summary_lines.append(
            f"Configured to sync `{payload.image_name}@{payload.digest}` to: "
            + ", ".join(f"`{destination}`" for destination in sync_destinations)
        )
    write_step_summary("\n".join(summary_lines))


def _preflight(
    config_path: Path,
    payload: ClientPayload,
    authorized_configs: list[ImageConfig],
    defaults: ConfigDefaults,
) -> None:
    """Validate the configs about to be applied, before writing any manifest.

    ``_apply_manifest`` writes each manifest as it goes, so a problem only the
    third manifest has — a path that doesn't exist, a ``set`` selector that
    doesn't resolve, a ``{sha}`` that should have been ``{git_sha}`` — used to
    surface as a traceback *after* the first two were already written, leaving
    a half-applied bump behind. Running the config validator over exactly the
    configs this run selected turns that into one readable, complete list of
    problems and a clean exit before anything changes on disk.

    Warnings are logged rather than fatal: they describe configs that work but
    probably don't do what their author meant, which is not a reason to fail a
    release. ``odp-releaser validate image-manifest --strict`` is where those
    are meant to block.
    """
    diagnostics = validate_image_configs(
        config_path,
        payload.image_name,
        authorized_configs,
        defaults,
        payload=payload,
    )
    for warning in diagnostics.warnings:
        logger.warning(warning.render())
    if not diagnostics.failed():
        return

    for error in diagnostics.errors:
        logger.error(error.render())
        typer.echo(error.render(), err=True)
    message = (
        f"{len(diagnostics.errors)} problem(s) in {config_path} would make "
        f"this bump of '{payload.image_name}' fail part-way through; no "
        "manifests were written. Run `odp-releaser validate image-manifest "
        f"{config_path}` to check the whole config."
    )
    logger.error(message)
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _resolve_config_setting[SettingT](
    authorized_configs: list[ImageConfig],
    default: SettingT | None,
    attr: str,
    image_name: str,
) -> SettingT | None:
    """Resolve a per-config setting across the matching configs.

    Each config's own value falls back to the ``defaults``-level value. A
    config that resolves to no value has no opinion; when the configs that do
    have one disagree, warn and use the first in config order (mirroring the
    mixed-``update_mode`` handling).
    """
    values: list[SettingT] = [
        resolved
        for image_config in authorized_configs
        if (resolved := resolve_setting(getattr(image_config, attr), default))
        is not None
    ]
    if not values:
        return default
    distinct = [value for i, value in enumerate(values) if value not in values[:i]]
    if len(distinct) > 1:
        logger.warning(
            f"Mixed {attr} values across matching configs for "
            f"{image_name}: {distinct}; using {values[0]!r}"
        )
    return values[0]


def _resolve_sync_destinations(
    authorized_configs: list[ImageConfig],
    defaults: ConfigDefaults,
    new_tag: str,
) -> list[str]:
    """Every distinct sync-enabled destination across the authorized configs.

    Deliberately *not* routed through :func:`_resolve_config_setting`. That
    helper's "first wins, log a warning" handling is the right call for a
    disagreement over a single value like ``environment`` -- there is only
    one environment to report. Sync destinations are not that: two authorized
    configs that both resolve ``sync`` true with different ``deployed_as``
    values are two independent registries that each expect a copy of this
    image, not two opinions about one setting. Mirroring to only the first
    (silently, or behind nothing louder than a warning) would leave the
    second registry serving a stale or missing tag until something else
    happens to notice -- an outage generator, not a convenience. So every
    authorized config that resolves ``sync`` true *and* has a ``deployed_as``
    contributes its own ``<deployed_as>:<new_tag>`` destination, and the
    caller copies to all of them.

    Two configs that declare the *same* destination are deduplicated, in
    config order -- that is one registry path being written to twice, not two
    -- using the same dedupe-preserving-order idiom as
    ``_resolve_config_setting``.
    """
    destinations = [
        f"{image_config.deployed_as}:{new_tag}"
        for image_config in authorized_configs
        if resolve_setting(image_config.sync, defaults.sync)
        and image_config.deployed_as
    ]
    return [
        destination
        for i, destination in enumerate(destinations)
        if destination not in destinations[:i]
    ]


def _config_authorizes(
    image_config: ImageConfig,
    defaults: ConfigDefaults,
    payload: ClientPayload,
    team_checker: "_TeamMembershipChecker",
) -> bool:
    """Whether this config's resolved allowlists accept the payload.

    Each allowlist falls back to the ``defaults``-level value when the config
    doesn't set its own; a resolved value of ``None`` disables that check. A
    rejected config is skipped with a warning so other configs for the image
    can still apply; ``team_checker`` caches GitHub team lookups across
    configs.
    """
    allowed_repos = resolve_setting(
        image_config.allowed_source_repos, defaults.allowed_source_repos
    )
    if allowed_repos is not None and payload.repo not in allowed_repos:
        logger.warning(
            f"Skipping a config for {payload.image_name}: source repository "
            f"'{payload.repo}' is not in its allowed_source_repos"
        )
        return False

    allowed_actors = resolve_setting(
        image_config.allowed_actors, defaults.allowed_actors
    )
    if allowed_actors is None:
        return True
    actor = payload.source.actor
    if actor.lower() in {user.lower() for user in allowed_actors.users}:
        return True
    if any(team_checker.actor_in_team(team, actor) for team in allowed_actors.teams):
        return True
    logger.warning(
        f"Skipping a config for {payload.image_name}: actor '{actor}' is "
        "not in its allowed_actors"
    )
    return False


def _authorization_error(message: str) -> NoReturn:
    logger.error(message)
    typer.echo(message, err=True)
    raise typer.Exit(1)


class _TeamMembershipChecker:
    """Check ``allowed_actors`` team membership with reporter app credentials.

    Teams live in the source orgs, so membership is checked with the same
    reporter app credentials (``REPORTER_APPS`` / ``REPORTER_APP_ID`` /
    ``REPORTER_APP_PRIVATE_KEY``) that ``report-deployment`` uses — the
    reporter app is the one installed on the source orgs. It additionally
    needs the organization ``Members: read`` permission there. Org tokens and
    membership results are cached for the run.
    """

    def __init__(self) -> None:
        self._membership: dict[tuple[str, str, str], bool] = {}
        self._org_tokens: dict[str, str] = {}

    def actor_in_team(self, team: str, actor: str) -> bool:
        """Whether ``actor`` is an active member of an ``org/team-slug`` team.

        Not being a member is an ordinary ``False``, but a check that cannot
        be evaluated — a malformed team entry, missing reporter credentials,
        or an API failure — exits with an error rather than silently
        rejecting, since the config asked for a check this run can't perform.
        """
        org, _, team_slug = team.partition("/")
        if not org or not team_slug:
            _authorization_error(
                f"allowed_actors team entry '{team}' must be an 'org/team-slug' pair"
            )

        key = (org, team_slug, actor.lower())
        if key not in self._membership:
            self._membership[key] = self._check(org, team_slug, actor)
        return self._membership[key]

    def _check(self, org: str, team_slug: str, actor: str) -> bool:
        try:
            if org not in self._org_tokens:
                creds = resolve_reporter_credentials(org)
                self._org_tokens[org] = org_installation_token_for(
                    creds, org, permissions={"members": "read"}
                )
            return is_team_member(org, team_slug, actor, self._org_tokens[org])
        except MissingCredentialsError as exc:
            _authorization_error(
                f"Checking allowed_actors team '{org}/{team_slug}' needs the "
                f"source org's reporter app credentials: {exc}"
            )
        except (AppNotInstalledOnOrgError, RequestFailed) as exc:
            _authorization_error(
                f"Could not check membership of '{actor}' in team "
                f"'{org}/{team_slug}': {exc}. The {org} org's reporter app "
                "must be installed there and granted the organization "
                "'Members: read' permission."
            )


def _pr_title_and_body(
    commit_message: list[str], metadata: ReportMetadata
) -> tuple[str, str]:
    """Derive a self-contained PR title/body pair from ``commit_message``.

    ``commit_message`` is always ``[title, "", "Triggered by ...", "", *body]``
    (see the assembly above), so the title is the first line and the body is
    everything after the blank separator line, with a trailing footer added so
    the PR body stands on its own without any workflow-side templating. The
    report ``metadata`` is appended as an invisible HTML comment so a
    merge-time `report-deployment --pr-body` run can finish the deployment
    report without any other context.
    """
    title = commit_message[0]
    body_lines = list(commit_message[2:])
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
    body_lines.extend(
        ["", "Automated image bump by odp-releaser.", "", embed_metadata(metadata)]
    )
    return title, "\n".join(body_lines)


if __name__ == "__main__":
    typer.run(bump_images)
