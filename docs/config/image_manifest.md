---
icon: lucide/pencil
---

# Image manifest

The image manifest is usually stored at `.github/image_manifest.yaml` in the deployment repos.

It can be tested with `odp-releaser test bump-images`.

It can also be statically checked -- schema, unknown keys, and semantic
mistakes like a bad `set` selector or template placeholder -- with
[`odp-releaser validate image-manifest`](../validation.md).

## Example image manifest

Example `image_manifest.yaml` with documentation can be generated via the CLI.

```bash
$ odp-releaser generate-config image-manifest
```

```python exec="on" result="yaml"
from odp_releaser.schemas.manifest_config import ManifestConfig

print(ManifestConfig.generate_yaml())
```

## API

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

### Manifests

For each of the manifest types, the `set` key takes a dictionary of [yamlpath](https://github.com/wwkimball/yamlpath#introduction) selectors and templated values to update.

#### Templated `set` values:

The values are templated with parts of the [client payload](../client_payload.md).

##### Example values

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
[Deploying from a mirrored registry](#deploying-from-a-mirrored-registry)
below.

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
      skip_local_inventory: true
```

### Deploying from a mirrored registry

Every manifest engine keys its update off the payload's own `image_name` by
default. Two `ImageConfig` settings change that when a deploy repo doesn't
deploy the payload's image name verbatim -- most commonly because it pulls
through a registry-native mirror instead of the upstream registry:

- **`deployed_as`** -- the image name the manifests under *this config*
  actually deploy from, when it differs from the payload's `image_name`.
  E.g. an ECR pull-through cache path such as
  `705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/sea-eagle-brown-3crs`
  mirroring upstream `gmri/sea-eagle-brown-3crs`. Unset falls back to the
  payload's `image_name`.
- **`sync`** (default `false`) -- whether odp-releaser should actually copy
  the payload's image to `deployed_as` before the bump is committed. See
  [Syncing images to another registry](../workflows.md#syncing-images-to-another-registry)
  for what runs and where credentials come from.

**`deployed_as` is deliberately per-config only.** Unlike every other setting
on `ImageConfig`, there is no `defaults.deployed_as`.

#### The common case: declare-only, no `sync`

Registry-native replication, an ECR pull-through cache being the usual
example here, populates itself the first time something pulls the
mirrored path, and a pull-through cache repository can't be pushed into at
all. So that is configured with `deployed_as` without `sync`:

```yaml
images:
  gmri/sea-eagle-brown-3crs:
    - events: [push]
      deployed_as: 705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/sea-eagle-brown-3crs
      kustomize_manifests:
        - ./kustomization.yaml
      helm_charts:
        - path: ./values.yaml
          dagster_user_code: true
```

#### Active mirroring: `sync: true`

When the destination registry does *not* replicate on its own (GHCR, Docker
Hub, or an ECR repository that isn't a pull-through cache), set `sync: true`
so odp-releaser copies the image, before the bump is committed:

```yaml
images:
  gmri/neracoos-mariners-dashboard:
    - events: [push]
      deployed_as: ghcr.io/gulfofmaine/neracoos-mariners-dashboard-dev
      sync: true
      kustomize_manifests:
        - path: apps/mariners-dev/kustomization.yaml
          pin: digest
```

A failed copy fails the bump. See
[Syncing images to another registry](../workflows.md#syncing-images-to-another-registry)
for the mechanics (`skopeo copy --all --preserve-digests`, a post-copy digest
check, skipping a destination that already carries the digest) and how to
wire up credentials.

#### Kustomize vs. Helm: where the mirror name lives

The two manifest engines carry a mirrored name in different places, so
`deployed_as` interacts with each differently -- this is the one thing worth
understanding well before relying on either:

- **Kustomize** keeps matching `/images[name="<payload image_name>"]` --
  the `images:` entry itself always stays keyed on the *upstream* name.
  Kustomize's own `newName` field is the mirror, so
  `deployed_as` must equal that entry's `newName`:

  ```yaml
  images:
    - name: gmri/sea-eagle-brown-3crs # upstream name -- always the match key
      newName: 705162855742.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/sea-eagle-brown-3crs # must equal deployed_as
      newTag: "ee1cadc"
  ```

  [`odp-releaser validate image-manifest`](../validation.md) checks this
  agreement: a missing or disagreeing `newName` is an error, and a `newName`
  with no `deployed_as` declared is a warning (the mirror would then be
  invisible to `sync` and to the Helm shorthand below).

- **The Helm dagster user-deployments shorthand** (`dagster_user_code: true`)
  instead matches `/deployments[image.repository="<deployed_as>"]`, because
  that chart's values layout has no `newName` equivalent -- `image.repository`
  *is* the deployed name. Before `deployed_as` existed, a values file whose
  `image.repository` already named a mirror could not be bumped at all:
  nothing selected it.

A config with both a kustomize manifest and a dagster-shorthand Helm manifest
for the same mirrored image needs no special coordination: `deployed_as` is
the one setting both engines key off, each in the place that engine actually
carries a mirror.

#### The `{deployed_image}` placeholder

`set` values -- on any of the three manifest types -- can also reference
`{deployed_image}`, the same name just described (`deployed_as` when set,
otherwise the payload's own `image_name`). This lets a `file_manifests` entry
write the mirror registry directly instead of hand-typing it:

```yaml
file_manifests:
  - path: ../apps/config/deployment.json
    set:
      /spec/template/spec/containers[0]/image: "{deployed_image}@{digest}"
```

[`odp-releaser validate image-manifest`](../validation.md) warns when a `set`
value hard-codes the *upstream* image name literally on a config that also
sets `deployed_as` -- almost always meant to be `{deployed_image}` instead.

!!! note "A shape limitation: no registry ports"

    `deployed_as` follows the same shape rule as an `images:` key: non-empty,
    trimmed, lowercase, and free of `@` and `:`. That last rule means a registry
    host with an explicit port (`registry.example.com:5000/foo`) currently can't
    be expressed as a `deployed_as` -- the same pre-existing limitation an
    `images:` key and a payload `image_name` already share.

### Pull request comments

When the reporter app has been granted `Pull requests: Read and write` (see
[GitHub Apps](../github_apps.md#pull-request-comments)), a bump also comments
on the **source** pull request saying which image landed and where. There are
two templates, because a bump has two states worth distinguishing:

- **`staged`** — a `pull_request`-mode bump has opened a pull request in the
  deploy repo but nothing is live yet.
- **`deployed`** — the bump has landed: immediately for `update_mode: commit`,
  or when the bump pull request merges, at which point
  [`report-merged.yml`](../workflows.md#report-merged) rewrites the staged
  comment in place.

Each comment is keyed to one `(deploy repo, image, environment)` triple, so a
rerun edits the same comment rather than adding another, and sibling bumps
never overwrite each other on the same pull request.

Both templates are inherited **field by field** — a config that overrides only
`staged` keeps the `defaults`-level `deployed`, and an unset field falls back to
the built-in template. That differs from every other setting, where a config's
value replaces the default wholesale, because the two templates describe
different states and are meant to be set independently.

Only `push` events carry a source pull request (that's the only event
`odp-releaser notify` resolves one for), so nothing is posted for release or
`workflow_dispatch` dispatches;
[`odp-releaser validate image-manifest`](../validation.md) warns when a config
asks for a comment it could never post. Set `enabled: false` to turn commenting
off, or a template to `""` to post nothing in that one state.

#### The built-in templates

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

#### Comment placeholders

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
`odp-releaser validate image-manifest` before a release, rather than failing
mid-bump.

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.CommentConfig
    options:
      heading_level: 4
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```
