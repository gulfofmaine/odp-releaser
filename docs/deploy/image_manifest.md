---
icon: lucide/pencil
---

# Image manifest

The image manifest is usually stored at `.github/image_manifest.yaml` in the
deployment repos. It is the config that
[`bump-images`](bump_images.md) reads to decide which manifests an incoming
image touches, how the change lands, and who was allowed to send it.

It can be dry-run against a canned payload with
[`odp-releaser test bump-images`](../development/testing.md#testing-an-image-manifest-config),
and statically checked with
[`odp-releaser validate image-manifest`](#validating).

## Example image manifest

Example `image_manifest.yaml` with documentation can be generated via the CLI.

```bash
$ odp-releaser generate-config image-manifest
```

```python exec="on" result="yaml"
from odp_releaser.schemas.manifest_config import ManifestConfig

print(ManifestConfig.generate_yaml())
```

## Manifests

For each of the manifest types, the `set` key takes a dictionary of [yamlpath](https://github.com/wwkimball/yamlpath#introduction) selectors and templated values to update.

### Templated `set` values:

The values are templated with parts of the [client payload](../api/client_payload.md).

#### Example values

```python exec="on"
from odp_releaser.bump_image_tester import EventType, load_client_payload

payload = load_client_payload(EventType.push)
for key, value in payload.value_format_kwargs().items():
    if isinstance(value, str):
        print(f"- `{key}` - `{value}`")
    else:
        print(f"- `{key}` - `{value}`")
```

One additional placeholder isn't in that list because it isn't part of the
payload: `{deployed_image}`, the name this config's manifests actually deploy
from (`deployed_as` when set, otherwise the payload's own `image_name`). See
[Syncing images](syncing.md#the-deployed_image-placeholder).

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.KustomizeManifest
    options:
      heading_level: 4
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.HelmManifest
    options:
      heading_level: 4
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.FileManifest
    options:
      heading_level: 4
      extensions:
      - griffe_pydantic
```

## Deploying from a mirrored registry

A config whose manifests deploy from a mirror rather than the payload's own
image name declares that with `deployed_as`, and can have odp-releaser push the
image there with `sync: true`. Both settings, how each manifest engine consumes
them, and the credentials a sync needs have their own page:
[Syncing images](syncing.md).

## Pull request comments

When the reporter app has been granted `Pull requests: Read and write` (see
[GitHub App](github_app.md#the-reporter-app)), a bump also comments on the
**source** pull request saying which image landed and where. There are two
templates, because a bump has two states worth distinguishing:

- **`staged`** — a `pull_request`-mode bump has opened a pull request in the
  deploy repo but nothing is live yet.
- **`deployed`** — the bump has landed: immediately for `update_mode: commit`,
  or when the bump pull request merges, at which point
  [`report-merged.yml`](report_merged.md) rewrites the staged comment in place.

Each comment is keyed to one `(deploy repo, image, environment)` triple, so a
rerun edits the same comment rather than adding another, and sibling bumps never
overwrite each other on the same pull request.

Both templates are inherited **field by field** — a config that overrides only
`staged` keeps the `defaults`-level `deployed`, and an unset field falls back to
the built-in template. That differs from every other setting, where a config's
value replaces the default wholesale, because the two templates describe
different states and are meant to be set independently.

Only `push` events carry a source pull request (that's the only event
`odp-releaser notify` resolves one for), so nothing is posted for release or
`workflow_dispatch` dispatches; [`odp-releaser validate image-manifest`](#validating)
warns when a config asks for a comment it could never post. Set `enabled: false`
to turn commenting off, or a template to `""` to post nothing in that one state.

### The built-in templates

These are what you get when neither level sets a template — and a useful
starting point for an override:

```python exec="on" result="yaml"
from odp_releaser.schemas.manifest_config import (
    DEFAULT_DEPLOYED_TEMPLATE,
    DEFAULT_STAGED_TEMPLATE,
)

print("comment:")
for field, template in (
    ("staged", DEFAULT_STAGED_TEMPLATE),
    ("deployed", DEFAULT_DEPLOYED_TEMPLATE),
):
    print(f"  {field}: |")
    for line in template.splitlines():
        print(f"    {line}".rstrip())
```

Rendered against a real bump, the `deployed` one comes out as:

```python exec="on" result="markdown" source="above"
from odp_releaser.bump_image_tester import EventType, load_client_payload
from odp_releaser.comment_body import CommentState, build_context, render_comment
from odp_releaser.schemas.manifest_config import DEFAULT_DEPLOYED_TEMPLATE

payload = load_client_payload(EventType.push)
context = build_context(
    payload,
    deploy_repo="gulfofmaine/deploy-repo",
    environment="production",
    environment_url=None,
    update_mode="commit",
    bump_url="https://github.com/gulfofmaine/deploy-repo/commit/9f8e7d6",
    run_url="https://github.com/gulfofmaine/deploy-repo/actions/runs/123",
    state=CommentState.deployed,
)
print(render_comment(DEFAULT_DEPLOYED_TEMPLATE, context))
```

That trailing HTML comment is the marker described above — it is invisible on
the rendered pull request, and it is how a rerun finds the comment to update.

### Comment placeholders

Comment templates are rendered with `str.format`, like `set` values — but
against their own, larger vocabulary, since a comment can reference deploy-side
facts that have no business being templated into a manifest:

```python exec="on"
from odp_releaser.comment_body import synthesize_context

for key, value in synthesize_context().format_kwargs().items():
    print(f"- `{{{key}}}`")
```

Because rendering goes through `str.format`, a **literal** brace has to be
doubled: write `{{` for `{` and `}}` for `}`. This matters for markdown that
contains a Helm or Go template, or a `${{ ... }}` expression:

```yaml
comment:
  deployed: |
    `{image_name}` is now `{new_tag}` in [{deploy_repo}]({bump_url}).

    Pinned with `{{{{ .Values.image.tag }}}}`.
```

An unknown placeholder or a stray single brace is reported by
[`odp-releaser validate image-manifest`](#validating) before a release, rather
than failing mid-bump.

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.CommentConfig
    options:
      heading_level: 4
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

## Validating

The config is read by pydantic models with the default `extra="ignore"`, so a
typo'd key (`kustomize_manifest:` instead of `kustomize_manifests:`) is silently
dropped rather than rejected. Nothing about that runtime path tells the author
their key never took effect. Worse, the checks that *would* catch a typo'd or
malformed value — a `yamlpath` selector that never resolves, a `{sha}`
placeholder that should have been `{git_sha}`, a manifest path that doesn't
exist — otherwise only run partway through a real bump, in CI, against a real
payload, often after earlier manifests in the same run have already been
written.

`odp-releaser validate image-manifest` runs all of those checks statically and
offline: no GitHub API calls, no writes, just the config file(s) on disk. It's
meant to run in CI on a config repo, as a
[pre-commit hook](../getting-started.md#pre-commit-hooks), or by hand before a
config change is merged.

```bash
odp-releaser validate image-manifest
odp-releaser validate image-manifest .github/image_manifest.yaml
```

A clean file prints a one-line `✓ <path>` to stdout. Problems are printed to
stderr as `path:line: severity: message`, one per line, colored by severity. The
command exits `1` if any file has errors, or — with `--strict` — if any file has
warnings, so a repo that wants warnings to block CI can opt in:

```bash
odp-releaser validate image-manifest --strict
```

`image-manifest` also accepts `--no-check-files`, for a repo where the manifests
an `image_manifest.yaml` points at (Kustomize/Helm/plain YAML files) don't live
in the same checkout the validator is run against:

```bash
odp-releaser validate image-manifest --no-check-files
```

### What is checked

Every check exists because a config that is *shaped* correctly can still *mean*
something the runtime code mishandles. The tables below list each check's
consequence at bump time — that's the reason it exists.

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
| A `comment` template uses an unknown placeholder, or a single stray `{`/`}` | `odp-releaser comment` can't render it; literal braces must be doubled (`{{`/`}}`). Comment templates have [their own vocabulary](#comment-placeholders), so a `set`-only placeholder like `{payload}` is wrong here |
| `kustomize_manifests[].pin: tag` but `/images[name=...]/newTag` doesn't exist | `bump-images` sets it with `mustexist=True`, which raises |
| `dagster_user_code: true` but no matching `/deployments[image.repository=...]` entry | `bump-images` sets its tag with `mustexist=True`, which raises |
| Referenced manifest file can't be read or parsed (unless `--no-check-files`) | `bump-images` fails the same way when it tries to load the file mid-run |
| `deployed_as` isn't a valid image name (empty, whitespace, uppercase, contains `@`/`:`) | Used as the Helm dagster shorthand's `image.repository` selector and in `set` templating via `{deployed_image}`, so it must be a plain image name like a real payload's `image_name` |
| `sync: true` (directly, or inherited from `defaults.sync`) with no `deployed_as` set | There is nothing for odp-releaser to copy the payload's image to |
| A kustomize manifest's `/images[name=...]` entry has no `newName`, but `deployed_as` is set | Kustomize would still render the upstream image, not the mirror the config declares |
| A kustomize manifest's `/images[name=...]/newName` disagrees with `deployed_as` | The manifest and the config disagree about which registry this image actually deploys from |

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
| The same resolved manifest path is targeted more than once for one event | `bump-images` applies all of them, redundantly |
| A `comment` is configured for an event that never carries a source pull request (anything but `push`) | There is nothing to comment on, so the comment can never be posted |
| `comment.staged` is set but `update_mode` resolves to `commit` | A direct commit is reported as deployed immediately, so only `comment.deployed` is ever used |
| Multiple configs matching the same event disagree on `update_mode` or a resolved setting (`environment`, `environment_url`, `reviewers`, `team_reviewers`, `comment`) | `bump-images` warns and silently uses the first config's value |
| `deployed_as` is set to the same value as this image's own `images:` key | Redundant: `ImageConfig.deployed_name` already falls back to the `images:` key when `deployed_as` is unset, so this declares nothing new |
| A kustomize manifest's `/images[name=...]/newName` is set but no `deployed_as` declares it | `sync` and the Helm dagster shorthand can't see this mirror |
| A `file_manifests` `set` value hard-codes the upstream image name while `deployed_as` is set on the same config | Almost certainly meant to reference `{deployed_image}` instead — otherwise the wrong registry gets written |
| Two configs writing the same resolved manifest path resolve different `deployed_as` values | The manifest can only agree with one mirror; at least one config is wrong about what it actually deploys |

### `bump-images` pre-flight

Before `bump-images` writes any manifest, it runs the same semantic checks
against exactly the configs it has already selected for this event (after
filtering by image name, event, and authorization) — see `_preflight` in
`src/odp_releaser/bump_images.py`. If that turns up any error, `bump-images`
prints them all and exits before writing anything; a problem in, say, the third
of five manifests can no longer surface as a mid-run traceback after the first
two were already written and left half-applied.

Warnings from the pre-flight are logged, not fatal — they describe configs that
work but probably don't do what their author meant, which isn't a reason to
block a release. `odp-releaser validate image-manifest --strict` is where
warnings are meant to block, in CI on the config repo itself.

## API Reference

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.ManifestConfig
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.ImageConfig
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```
