---
icon: lucide/blocks
---

# Composite Actions

Alongside the [reusable workflows](workflows.md), `odp-releaser` ships two
composite GitHub Actions for deploy repos that need more control than
`bump-images.yml` offers — most commonly to run extra steps *after* the bump
(e.g. syncing the freshly published image to another registry) before
anything is committed.

Both actions live in this repo and are referenced with the standard
`owner/repo/path@ref` syntax:

```yaml
uses: gulfofmaine/odp-releaser/.github/actions/install@<sha-or-tag>
uses: gulfofmaine/odp-releaser/.github/actions/bump_images@<sha-or-tag>
```

`bump-images.yml` and `notify.yml` use these same actions internally, checked
out at `${{ job.workflow_sha }}` so the actions (and the CLI they install)
always match the workflow ref the caller pinned.

## `install`

Installs the `odp-releaser` CLI with [uv](https://docs.astral.sh/uv/). The
CLI is installed from the action's own repository files, so the CLI version
always matches the action ref — pinning the `uses:` reference is enough to
pin the CLI too.

```yaml
- name: Install ODP Releaser
  uses: gulfofmaine/odp-releaser/.github/actions/install@<sha-or-tag>
  # with:
  #   install_uv: "false" # if the job already provides uv on the PATH
```

### Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `install_uv` | no | `"true"` | Whether to install uv with `astral-sh/setup-uv`. Set to `"false"` when the job already provides uv on the PATH. |
| `cache_suffix` | no | `odp-releaser-${{ github.action_ref }}` | Suffix for setup-uv's cache key, keeping the uv cache keyed to the odp-releaser version being installed. Only used when `install_uv` is `"true"`. |

## `bump_images`

Runs `odp-releaser bump-images` against the repository_dispatch
`client_payload` and the checked-out deploy repo's image manifest config,
then — depending on the image's `update_mode` — commits the change directly
or opens a pull request, exactly like `bump-images.yml`.

Prerequisites:

- The deploy repo is checked out, with credentials that can push (unless
  `stage_only` is `"true"`).
- The `odp-releaser` CLI is on the PATH — run the `install` action first. A
  composite action cannot reference a sibling local action itself (relative
  `uses:` paths resolve against the workflow's workspace, not the action's
  repo — [actions/runner#1348](https://github.com/actions/runner/issues/1348)),
  so the two actions compose in your workflow.

### `stage_only`: bump without committing

Set `stage_only: "true"` to write the manifest changes and `git add` them
**without** making a commit or opening a pull request (the image's
`update_mode` is ignored). Your workflow then owns the follow-up: add
whatever steps you need — the action's outputs carry the image name and
digest — and commit the staged changes yourself.

```yaml
on:
  repository_dispatch:
    types: [image-published]

jobs:
  bump:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@<sha> # v7

      - name: Install ODP Releaser
        uses: gulfofmaine/odp-releaser/.github/actions/install@<sha-or-tag>

      - name: Bump images
        id: bump
        uses: gulfofmaine/odp-releaser/.github/actions/bump_images@<sha-or-tag>
        with:
          stage_only: "true"

      - name: Sync image to the deploy registry
        if: steps.bump.outputs.changed == 'true'
        env:
          IMAGE_NAME: ${{ steps.bump.outputs.image_name }}
          DIGEST: ${{ steps.bump.outputs.digest }}
        run: |
          crane copy "$IMAGE_NAME@$DIGEST" "registry.example.com/${IMAGE_NAME#*/}"

      - name: Commit bump
        if: steps.bump.outputs.changed == 'true'
        env:
          COMMIT_MESSAGE: ${{ steps.bump.outputs.commit_message }}
        run: |
          git config user.name "odp-releaser[bot]"
          git config user.email "odp-releaser[bot]@users.noreply.github.com"
          git commit -m "$COMMIT_MESSAGE"
          git push
```

### Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `client_payload` | no | `${{ toJSON(github.event.client_payload) }}` | `repository_dispatch` client_payload JSON produced by `odp-releaser notify`. Defaults to the payload of the workflow's triggering event. |
| `config_path` | no | `.github/image_manifest.yaml` | Path to the image manifest config file. |
| `verbosity` | no | `"1"` | CLI verbosity: `0`=warning, `1`=info (default), `2`+=debug. Maps to the CLI's `-v`/`-vv`/`-vvv` flags (capped at 3). |
| `git_user_name` | no | `odp-releaser[bot]` | Git author/committer name for direct commits. |
| `git_user_email` | no | `odp-releaser[bot]@users.noreply.github.com` | Git author/committer email for direct commits. |
| `stage_only` | no | `"false"` | When `"true"`, write the manifest changes and `git add` them, but make no commit and open no pull request. |
| `token` | no | `${{ github.token }}` | Token used to push the bump commit or open the pull request. Pass an app-minted token if the resulting commit/PR should trigger CI (see [the `ci_app_*` note](workflows.md#the-ci_app_-pr-ci-triggering-note)). |

### Outputs

| Output | Description |
| --- | --- |
| `image_name` | Image name the bump ran for (no tag or digest). |
| `digest` | Digest (`sha256:...`) of the image the bump ran for. |
| `changed` | Whether any manifests changed (`"true"`/`"false"`). |
| `update_mode` | Update mode resolved from the image manifest config (`"commit"`/`"pull_request"`). |
| `branch_name` | Branch name used for `pull_request` mode. |
| `commit_message` | Generated commit message for the bump. |
| `pr_title` | Generated pull request title for the bump. |
| `pr_body` | Generated pull request body for the bump. |
