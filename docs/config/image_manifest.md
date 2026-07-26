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
