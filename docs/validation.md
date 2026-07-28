---
icon: lucide/shield-check
---

# Validation

`image_manifest.yaml` and `deploy_targets.yaml` are read by pydantic models
with the default `extra="ignore"`, so a typo'd key (`kustomize_manifest:`
instead of `kustomize_manifests:`, `repoo:` instead of `repo:`) is silently
dropped rather than rejected. Nothing about that runtime path tells the
author their key never took effect. Worse, the checks that *would* catch a
typo'd or malformed value -- a `yamlpath` selector that never resolves, a
`{sha}` placeholder that should have been `{git_sha}`, a manifest path that
doesn't exist -- only run partway through a real bump or dispatch, in CI,
against a real payload, often after earlier manifests in the same run have
already been written.

`odp-releaser validate` runs all of those checks statically and offline: no
GitHub API calls, no writes, just the config file(s) on disk. It's meant to
run in CI on a config repo, as a pre-commit hook, or by hand before a config
change is merged.

## Usage

```bash exec="on" result="ansi" width="80"
odp-releaser validate --help
```

Each subcommand defaults to this project's usual config path and accepts one
or more explicit paths instead:

```bash
odp-releaser validate image-manifest
odp-releaser validate image-manifest .github/image_manifest.yaml
odp-releaser validate deploy-targets
odp-releaser validate deploy-targets .github/deploy_targets.yaml
```

A clean file prints a one-line `✓ <path>` to stdout. Problems are printed to
stderr as `path:line: severity: message`, one per line, colored by severity.
The command exits `1` if any file has errors, or -- with `--strict` -- if any
file has warnings, so a repo that wants warnings to block CI can opt in:

```bash
odp-releaser validate image-manifest --strict
```

`image-manifest` also accepts `--no-check-files`, for a repo where the
manifests an `image_manifest.yaml` points at (Kustomize/Helm/plain YAML
files) don't live in the same checkout the validator is run against:

```bash
odp-releaser validate image-manifest --no-check-files
```

## What is checked

Every check exists because a config that is *shaped* correctly can still
*mean* something the runtime code mishandles. The tables below list each
check's consequence at bump or dispatch time -- that's the reason it exists.

### `image_manifest.yaml`

**Errors** (always fail the run):

| Check | Consequence if not caught |
| --- | --- |
| Unknown key anywhere in the config | The key is silently dropped by pydantic; the setting the author intended never takes effect |
| Schema validation failure (wrong type, missing required field, ...) | `bump-images` never gets a validated config to run against at all |
| `images` key isn't a valid image name (empty, whitespace, uppercase, contains `@`/`:`) | Can never equal a real payload's `image_name`, so this config can never match a bump |
| `allowed_source_repos` entry isn't an `owner/name` pair | Can never equal the payload's repo, so this entry can never match |
| `allowed_actors.teams` entry isn't an `org/team-slug` pair | The team-membership check hard-exits the whole run |
| `team_reviewers` entry has an `org/` prefix | GitHub's "request review from teams" API expects a bare slug; the request fails |
| A `set` selector is not a valid yamlpath | `bump-images` fails trying to apply the same selector |
| A `set` selector does not resolve against its target manifest | `Processor.get_nodes(..., mustexist=True)` raises at bump time |
| A templated value uses a positional (`{0}`, `{}`), attribute/index (`{payload.foo}`), or unknown placeholder | `str.format(**kwargs)` raises `KeyError`/`ValueError` at bump time |
| A templated value fails to actually format against a real payload | Same failure, confirmed against real `value_format_kwargs()` |
| A `comment` template uses an unknown placeholder, or a single stray `{`/`}` | `odp-releaser comment` can't render it; literal braces must be doubled (`{{`/`}}`). Comment templates have [their own vocabulary](config/image_manifest.md#comment-placeholders), so a `set`-only placeholder like `{payload}` is wrong here |
| `kustomize_manifests[].pin: tag` but `/images[name=...]/newTag` doesn't exist | `bump-images` sets it with `mustexist=True`, which raises |
| Referenced manifest file can't be read or parsed (unless `--no-check-files`) | `bump-images` fails the same way when it tries to load the file mid-run |

**Warnings** (reported; fail the run only with `--strict`):

| Check | Consequence if not caught |
| --- | --- |
| `allowed_source_repos` is explicitly `[]` | Denies every source repository, probably not what was intended |
| `allowed_actors` is present but both `users` and `teams` are empty | Denies every actor |
| A config has none of `kustomize_manifests`, `helm_charts`, `file_manifests` | The config is a silent no-op |
| `reviewers`/`team_reviewers` set but `update_mode` resolves to `commit` | Only `pull_request` mode ever requests reviewers; these are never used |
| A templated value has no placeholder at all | `bump-images` can never change this value |
| `kustomize_manifests[].pin: digest` but no `/images[name=...]` entry exists yet | Set with `mustexist=False`, which won't create the missing entry |
| Both `newTag` and `digest` set on the same kustomize image entry | Kustomize prefers `digest`; bumping the tag has no visible effect |
| `dagster_user_code: true` but no matching `/deployments[image.repository=...]` entry | `bump-images` only logs a warning and leaves the file unchanged |
| The same resolved manifest path is targeted more than once for one event | `bump-images` applies all of them, redundantly |
| A `comment` is configured for an event that never carries a source pull request (anything but `push`) | There is nothing to comment on, so the comment can never be posted |
| `comment.staged` is set but `update_mode` resolves to `commit` | A direct commit is reported as deployed immediately, so only `comment.deployed` is ever used |
| Multiple configs matching the same event disagree on `update_mode` or a resolved setting (`environment`, `environment_url`, `reviewers`, `team_reviewers`, `comment`) | `bump-images` warns and silently uses the first config's value |

### `deploy_targets.yaml`

**Errors:**

| Check | Consequence if not caught |
| --- | --- |
| File missing, empty, unparsable, or a schema mismatch | `notify` cannot load any targets at all |
| Unknown key on a target | Silently dropped by pydantic (e.g. a typo'd `repoo:` dispatches with no repo) |
| `owner` or `repo` empty/whitespace-only | `repository_dispatch` has nothing to target |
| `repo` contains `/` | The owner belongs in `owner`; as written, dispatch is sent to a repository that doesn't exist |
| `owner`/`repo` contains characters GitHub doesn't allow, or starts/ends with `-` | Can never match a real organization, user, or repository name |
| `event_type` empty/whitespace-only | `repository_dispatch` requires a non-empty `event_type` |

**Warnings:**

| Check | Consequence if not caught |
| --- | --- |
| Two targets share the same `(owner, repo, event_type)` triple | The same dispatch is sent twice |

## `bump-images` pre-flight

Before `bump-images` writes any manifest, it runs the same semantic checks
against exactly the configs it has already selected for this event (after
filtering by image name, event, and authorization) -- see `_preflight` in
`src/odp_releaser/bump_images.py`. If that turns up any error, `bump-images`
prints them all and exits before writing anything; a problem in, say, the
third of five manifests can no longer surface as a mid-run traceback after
the first two were already written and left half-applied.

Warnings from the pre-flight are logged, not fatal -- they describe configs
that work but probably don't do what their author meant, which isn't a
reason to block a release. `odp-releaser validate image-manifest --strict`
is where warnings are meant to block, in CI on the config repo itself.

## Pre-commit hooks

This project ships pre-commit hooks that run the same checks. In a consumer
repo's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/gulfofmaine/odp-releaser
    rev: "<pin to a released tag or commit SHA>"
    hooks:
      - id: validate-image-manifest
      - id: validate-deploy-targets
```

Each hook's default `files:` pattern only matches
`.github/image_manifest.ya?ml` or `.github/deploy_targets.ya?ml`
respectively; override `files:` on the hook entry if a config lives
somewhere else.

## Editor and JSON Schema support

`schemas/image_manifest.schema.json` and `schemas/deploy_targets.schema.json`
are generated from the same pydantic models
(`odp-releaser generate-config schema image-manifest|deploy-targets`) and
published alongside this project. Unlike the models themselves, the
generated schemas set `additionalProperties: false` on every object, so a
generic JSON Schema validator rejects unknown keys too.

Point an editor at them with a
[yaml-language-server](https://github.com/redhat-developer/yaml-language-server)
modeline at the top of the config file, for inline completion and
error-squiggles:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/gulfofmaine/odp-releaser/main/schemas/image_manifest.schema.json
```

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/gulfofmaine/odp-releaser/main/schemas/deploy_targets.schema.json
```

Pin the URL to a released tag instead of `main` if reproducibility across
time matters more than always tracking the latest schema.

For a repo already using
[`check-jsonschema`](https://check-jsonschema.readthedocs.io/) in pre-commit
rather than (or alongside) the hooks above:

```yaml
repos:
  - repo: https://github.com/python-jsonschema/check-jsonschema
    rev: "<pin to a released tag or commit SHA>"
    hooks:
      - id: check-jsonschema
        name: Validate image_manifest.yaml
        files: ^\.github/image_manifest\.ya?ml$
        args:
          [
            "--schemafile",
            "https://raw.githubusercontent.com/gulfofmaine/odp-releaser/main/schemas/image_manifest.schema.json",
          ]
      - id: check-jsonschema
        name: Validate deploy_targets.yaml
        files: ^\.github/deploy_targets\.ya?ml$
        args:
          [
            "--schemafile",
            "https://raw.githubusercontent.com/gulfofmaine/odp-releaser/main/schemas/deploy_targets.schema.json",
          ]
```

The schema check and `odp-releaser validate` are complementary, not
redundant: the schema is strict about shape and unknown keys but knows
nothing about this project's runtime behavior, while `odp-releaser validate`
is the more thorough check -- it also verifies that referenced manifest
files exist and parse, that `yamlpath` selectors actually resolve against
them, and that templated values format successfully.

## What is not checked

Nothing here makes a network call. In particular, `odp-releaser validate`
never checks whether an image tag or digest actually exists in a registry,
whether a `reviewers`/`team_reviewers`/`allowed_actors` entry names a real
GitHub user, team, or repository, or whether a deploy repo's workflow is
actually listening for a `deploy_targets.yaml` entry's `event_type`. Those
all require calling GitHub (or a registry) at the time of a real release,
which is exactly what this validator is meant to avoid depending on.
