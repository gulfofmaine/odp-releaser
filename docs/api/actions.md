---
icon: lucide/blocks
---

# Composite Actions

Making up the reusable workflows ([`notify`](../source/notify.md),
[`bump-images`](../deploy/bump_images.md), and
[`report-merged`](../deploy/report_merged.md)), `odp-releaser` ships four
composite GitHub Actions for deploy repos that need more control than
`bump-images.yml` offers — most commonly to run extra steps *after* the bump
(e.g. syncing the freshly published image to another registry that isn't natively reported) before
anything is committed, report deployments and pull request comments back
to source repos from a custom workflow, or triggering additional deployment steps.

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

::: .github/actions/install
    handler: github

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
  then not in that file, and skopeo won't resolve them. See
  [Syncing images](../deploy/syncing.md) for what the sync does and when to
  ask for one.

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

??? note "`bump_images.yml` reference"

    ::: .github/actions/bump_images
        handler: github

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
  [GitHub Apps](github_apps.md#the-reporter-role). The minted token is scoped to
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

(That example is what
[`report-merged.yml`](../deploy/report_merged.md)
packages up — prefer the reusable workflow unless you need to customize it.)

??? note "`report_deployment.yml` Reference"

    ::: .github/actions/report_deployment
        handler: github

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
[Pull request comments](../deploy/image_manifest.md#pull-request-comments) for the
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

??? note "`comment_on_pr.yml` reference"

    ::: .github/actions/comment_on_pr
        handler: github
