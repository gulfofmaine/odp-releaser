---
icon: lucide/blocks
---

# Composite Actions

Alongside the [reusable workflows](workflows.md), `odp-releaser` ships four
composite GitHub Actions for deploy repos that need more control than
`bump-images.yml` offers — most commonly to run extra steps *after* the bump
(e.g. syncing the freshly published image to another registry) before
anything is committed, or to report deployments and pull request comments back
to source repos from a custom workflow.

The actions live in this repo and are referenced with the standard
`owner/repo/path@ref` syntax:

```yaml
uses: gulfofmaine/odp-releaser/.github/actions/install@<sha-or-tag>
uses: gulfofmaine/odp-releaser/.github/actions/bump_images@<sha-or-tag>
uses: gulfofmaine/odp-releaser/.github/actions/report_deployment@<sha-or-tag>
uses: gulfofmaine/odp-releaser/.github/actions/comment_on_pr@<sha-or-tag>
```

The reusable workflows use these same actions internally, checked out at
`${{ job.workflow_sha }}` so the actions (and the CLI they install) always
match the workflow ref the caller pinned.

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
- Only when the image manifest asks for a sync (`deployed_as` with
  `sync: true`): `skopeo` on the PATH (preinstalled on GitHub-hosted ubuntu
  runners), and the destination registry already logged in to by an earlier
  step (`docker/login-action`, or `configure-aws-credentials` +
  `amazon-ecr-login`). skopeo reads those credentials from
  `$HOME/.docker/config.json` via the containers credential search order, so
  no separate `skopeo login` step is needed. A caller that configures a
  Docker *credential helper* instead is the exception, the credentials are
  then not in that file, and skopeo won't resolve them.

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
          NEW_TAG: ${{ steps.bump.outputs.new_tag }}
        run: |
          # The destination needs the tag the bump just wrote. Without it the
          # copy lands as `:latest` while the manifest points at `newTag:
          # <tag>`, so the deploy reads a tag nothing ever pushed.
          crane copy "$IMAGE_NAME@$DIGEST" \
            "registry.example.com/${IMAGE_NAME#*/}:$NEW_TAG"

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
| `sync` | no | `"true"` | When `"false"`, skip the image sync even for configs whose `sync: true` asks for one. The sync otherwise runs whenever the image manifest declares a `deployed_as` with `sync: true`, between writing the manifests and committing them. Requires `skopeo` and a logged in destination registry. |
| `dry_run` | no | `"false"` | Testing aid: run the CLI with `--dry-run` (no manifest files written) and skip the stage, commit, and pull-request steps. Outputs are still produced. |
| `token` | no | `${{ github.token }}` | Token used to push the bump commit or open the pull request. Pass an app-minted token if the resulting commit/PR should trigger CI (see [the `ci_app_*` note](workflows.md#the-ci_app_-pr-ci-triggering-note)). When the image manifest configures `team_reviewers`, the token also needs organization "Members: read" to request the team reviews. |
| `reporter_apps` | no | `""` | JSON object mapping source `owner -> {app_id, private_key}` reporter app credentials, used to check `allowed_actors` team membership against source orgs (the app needs organization "Members: read" there). |
| `reporter_app_id` | no | `""` | App ID of the source org's reporter GitHub App, used to check `allowed_actors` team membership. Fallback for owners not in `reporter_apps`. |
| `reporter_app_private_key` | no | `""` | Private key matching `reporter_app_id`. |

### Outputs

| Output | Description |
| --- | --- |
| `image_name` | Image name the bump ran for (no tag or digest). |
| `digest` | Digest (`sha256:...`) of the image the bump ran for. |
| `new_tag` | Tag the manifests were bumped to. For a `release` event this is the release ref, not the payload's `tag` — prefer this over reading the client payload directly. |
| `changed` | Whether any manifests changed (`"true"`/`"false"`). |
| `update_mode` | Update mode resolved from the image manifest config (`"commit"`/`"pull_request"`). |
| `environment` | GitHub environment name resolved from the image manifest config for deployment reporting; empty when unconfigured. |
| `environment_url` | Deployment "View deployment" URL resolved (and templated) from the image manifest config; empty when unconfigured. |
| `pull_request_url` | URL of the bump pull request; empty unless a `pull_request`-mode bump opened or updated one. |
| `branch_name` | Branch name used for `pull_request` mode. |
| `commit_message` | Generated commit message for the bump. |
| `pr_title` | Generated pull request title for the bump. |
| `pr_body` | Generated pull request body for the bump (includes the embedded [report metadata](#report_deployment)). |
| `reviewers` | Comma-separated GitHub usernames requested as reviewers on the bump pull request; empty when none are configured. |
| `team_reviewers` | Comma-separated GitHub team slugs requested as reviewers on the bump pull request; empty when none are configured. |
| `comment_enabled` | Whether commenting back on the source pull request is enabled for this image (`"true"`/`"false"`). |
| `comment_pr_number` | Source pull request the comment lands on; empty for events that carry no pull request (`release`, `workflow_dispatch`). |
| `comment_staged_template` | Resolved comment template for a bump pull request awaiting review, unrendered — pass to [`comment_on_pr`](#comment_on_pr). |
| `comment_deployed_template` | Resolved comment template for a landed bump, unrendered. |
| `sync_source_ref` | Image reference the sync copies from, pinned by digest; empty when no config asked for a sync. |
| `sync_destinations` | Newline-separated destination references the image was (or would be) copied to; empty when no config asked for a sync. |
| `synced` | Whether the sync step actually ran to completion (`"true"`/`"false"`). `"false"` when nothing was configured, the sync was skipped (`sync: "false"`), or this was a dry run. |

## `report_deployment`

Runs `odp-releaser report-deployment`, which creates (or finds) a
[GitHub deployment](https://docs.github.com/en/rest/deployments/deployments)
on the **source** repository at the commit that built the image and sets its
status — `success` for a bump committed directly, `queued` for a bump pull
request that still needs review. `bump-images.yml` runs this action after a
successful bump, and `report-merged.yml` runs it when a bump PR merges; use
it directly when composing your own workflow from the `bump_images` action.

Provide exactly one of:

- `client_payload` — right after a bump, the same payload the bump ran with;
- `pr_body` — after a bump pull request closed, the body of that PR. The
  payload, environment, and environment URL that `bump_images` embedded in
  the body at bump time are read back out, and the queued deployment from
  the bump is found (same commit + environment) and updated instead of a
  duplicate being created. A body without embedded metadata is a friendly
  no-op, so running on any closed PR is safe.

Prerequisites:

- The `odp-releaser` CLI is on the PATH — run the `install` action first
  (same sibling-action composition as `bump_images`).
- Reporter app credentials for the source org — see
  [GitHub Apps](github_apps.md#reporter-apps). The minted token is scoped to
  the single source repository with `deployments: write` only.

A failed report exits non-zero and fails the step; wrap the action in
`continue-on-error: true` (as `bump-images.yml` does) when reporting should
be best-effort rather than a hard failure.

```yaml
on:
  pull_request:
    types: [closed]

jobs:
  report:
    if: >-
      github.event.pull_request.merged == true &&
      startsWith(github.event.pull_request.head.ref, 'odp-releaser/')
    runs-on: ubuntu-latest
    steps:
      - name: Install ODP Releaser
        uses: gulfofmaine/odp-releaser/.github/actions/install@<sha-or-tag>

      - name: Report merged deployment
        uses: gulfofmaine/odp-releaser/.github/actions/report_deployment@<sha-or-tag>
        with:
          pr_body: ${{ github.event.pull_request.body }}
          environment_url: >-
            ${{ github.server_url }}/${{ github.repository }}/commit/${{
            github.event.pull_request.merge_commit_sha }}
          reporter_app_id: ${{ secrets.REPORTER_APP_ID }}
          reporter_app_private_key: ${{ secrets.REPORTER_APP_PRIVATE_KEY }}
```

(That example is exactly what [`report-merged.yml`](workflows.md#report-merged)
packages up — prefer the reusable workflow unless you need to customize it.)

### Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `client_payload` | one of these | `""` | `repository_dispatch` client_payload JSON produced by `odp-releaser notify`. |
| `pr_body` | one of these | `""` | Body of a merged bump pull request carrying the embedded report metadata. |
| `update_mode` | no | `commit` | How the bump landed: `commit` reports a `success` deployment, `pull_request` reports a `queued` one. |
| `environment` | no | `""` | GitHub environment name for the deployment. An environment embedded in `pr_body` wins; empty falls back to the deploy repo's `owner/name` slug. |
| `environment_url` | no | `""` | "View deployment" link for the deployment status — typically the bump commit or pull request URL. A URL embedded in `pr_body` wins. |
| `verbosity` | no | `"1"` | CLI verbosity: `0`=warning, `1`=info (default), `2`+=debug. Maps to the CLI's `-v`/`-vv`/`-vvv` flags (capped at 3). |
| `reporter_apps` | no | `""` | JSON object mapping source `owner -> {app_id, private_key}` reporter app credentials, for deploy repos that report to multiple source orgs. |
| `reporter_app_id` | no | `""` | App ID of the reporter GitHub App installed on the source repos. |
| `reporter_app_private_key` | no | `""` | Private key matching `reporter_app_id`. |

## `comment_on_pr`

Runs `odp-releaser comment`, which posts (or updates) a markdown comment on
the **source** repository's pull request saying which image was bumped and
where — the readable counterpart to the deployment record.
`bump-images.yml` runs this action after the deployment report, and
`report-merged.yml` runs it when a bump PR merges; use it directly when
composing your own workflow from the `bump_images` action.

Which template is used follows `update_mode`: `pull_request` posts the `staged`
comment (the bump is waiting on review, nothing is live), `commit` posts the
`deployed` one. See
[Pull request comments](config/image_manifest.md#pull-request-comments) for the
templates and their placeholders.

Provide exactly one of:

- `client_payload` — right after a bump, the same payload the bump ran with,
  with the templates passed in from the `bump_images` outputs;
- `pr_body` — after a bump pull request closed, the body of that PR. The
  payload, environment, comment templates and source pull request number that
  `bump_images` embedded at bump time are read back out, so no image manifest
  (and no deploy-repo checkout) is needed. A body without embedded metadata, or
  one from before comment support, is a friendly no-op.

Reruns **update the same comment** rather than adding another: it is found by
an invisible marker keyed on the deploy repo, the image, and the environment,
so another deploy repo's or another image's comment on the same pull request is
never touched.

Prerequisites:

- The `odp-releaser` CLI is on the PATH — run the `install` action first
  (same sibling-action composition as `bump_images`).
- Reporter app credentials for the source org, whose app has been granted
  `Pull requests: Read and write` **and whose installations have accepted that
  permission** — see
  [Pull request comments](github_apps.md#pull-request-comments). The minted
  token is scoped to the single source repository with `pull_requests: write`
  only.

Nothing is posted, and the step still succeeds, when `comment_enabled` is
`"false"`, when the chosen template is empty, or when there is no source pull
request to comment on (only `push` payloads carry one). Other failures exit
non-zero; wrap the action in `continue-on-error: true` (as `bump-images.yml`
does) when commenting should be best-effort.

```yaml
- name: Install ODP Releaser
  uses: gulfofmaine/odp-releaser/.github/actions/install@<sha-or-tag>

- name: Bump images
  id: bump
  uses: gulfofmaine/odp-releaser/.github/actions/bump_images@<sha-or-tag>

- name: Comment on the source pull request
  if: steps.bump.outputs.comment_pr_number != ''
  continue-on-error: true
  uses: gulfofmaine/odp-releaser/.github/actions/comment_on_pr@<sha-or-tag>
  with:
    update_mode: ${{ steps.bump.outputs.update_mode }}
    environment: ${{ steps.bump.outputs.environment }}
    bump_url: ${{ steps.bump.outputs.pull_request_url }}
    pr_number: ${{ steps.bump.outputs.comment_pr_number }}
    comment_enabled: ${{ steps.bump.outputs.comment_enabled }}
    staged_template: ${{ steps.bump.outputs.comment_staged_template }}
    deployed_template: ${{ steps.bump.outputs.comment_deployed_template }}
    reporter_app_id: ${{ secrets.REPORTER_APP_ID }}
    reporter_app_private_key: ${{ secrets.REPORTER_APP_PRIVATE_KEY }}
```

### Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `client_payload` | one of these | `""` | `repository_dispatch` client_payload JSON produced by `odp-releaser notify`. |
| `pr_body` | one of these | `""` | Body of a merged bump pull request carrying the embedded report metadata. |
| `update_mode` | no | `commit` | How the bump landed: `commit` posts the deployed comment, `pull_request` posts the staged one. |
| `environment` | no | `""` | Environment named in the comment, and part of the comment's identity. An environment embedded in `pr_body` wins; empty falls back to the deploy repo's `owner/name` slug. |
| `environment_url` | no | `""` | Available to templates as `{environment_url}`. A URL embedded in `pr_body` wins; empty falls back to `bump_url`. |
| `bump_url` | no | `""` | Where the bump lives — the bump commit or pull request URL — available to templates as `{bump_url}`. |
| `run_url` | no | `""` | Available to templates as `{run_url}`. Empty uses this workflow run; set it to point at a different one. |
| `pr_number` | no | `""` | Source pull request to comment on (`bump_images`' `comment_pr_number` output). Empty falls back to the payload's own pull request; none at all is a no-op. |
| `comment_enabled` | no | `"true"` | Whether to comment at all (`bump_images`' `comment_enabled` output). `"false"` is a no-op. |
| `staged_template` | no | `""` | Comment body for a `pull_request`-mode bump. A template embedded in `pr_body` wins. |
| `deployed_template` | no | `""` | Comment body for a landed bump. A template embedded in `pr_body` wins. |
| `verbosity` | no | `"1"` | CLI verbosity: `0`=warning, `1`=info (default), `2`+=debug. Maps to the CLI's `-v`/`-vv`/`-vvv` flags (capped at 3). |
| `reporter_apps` | no | `""` | JSON object mapping source `owner -> {app_id, private_key}` reporter app credentials, for deploy repos that report to multiple source orgs. |
| `reporter_app_id` | no | `""` | App ID of the reporter GitHub App installed on the source repos; needs `Pull requests: Read and write`. |
| `reporter_app_private_key` | no | `""` | Private key matching `reporter_app_id`. |
