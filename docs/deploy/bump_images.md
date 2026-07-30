---
icon: lucide/arrow-big-up-dash
---

# Bump images workflow

Runs in the **deployment** repo, triggered by the `repository_dispatch` event that
[`notify`](../source/notify.md) sends. It matches the incoming image against
[`.github/image_manifest.yaml`](image_manifest.md) and either commits the
updated manifests directly or opens a pull request, depending on that image's
`update_mode`.

An image with no entry at all in `images` is treated as a configuration error:
`bump-images` exits non-zero and lists the images that are configured. An image
that has an entry but an empty list of configs is a deliberate no-op and
succeeds without changes.

## Caller example

```yaml
on:
  repository_dispatch:
    types: [image-published]

concurrency:
  group: bump-images-${{ github.event.client_payload.image_name }}
  cancel-in-progress: false

jobs:
  bump:
    permissions:
      contents: write
      pull-requests: write
      id-token: write # required even if this deploy repo never syncs
    uses: gulfofmaine/odp-releaser/.github/workflows/bump-images.yml@<sha>
    with:
      # config_path: .github/image_manifest.yaml            # optional
      # git_user_name: odp-releaser[bot]                    # optional
      # git_user_email: odp-releaser[bot]@users.noreply.github.com
      # verbosity: 1                                       # optional, default
    secrets:
      ci_app_id: ${{ secrets.CI_APP_ID }} # optional
      ci_app_private_key: ${{ secrets.CI_APP_PRIVATE_KEY }} # optional
      reporter_app_id: ${{ secrets.REPORTER_APP_ID }} # optional
      reporter_app_private_key: ${{ secrets.REPORTER_APP_PRIVATE_KEY }} # optional
      # reporter_apps: ${{ secrets.REPORTER_APPS }}    # optional multi-org
```

Set the `concurrency` group at the **caller** level too (as above) — a burst of
dispatches for the same image shouldn't run two bump jobs in parallel and race
each other's commits. The reusable workflow itself also sets a job-level
`concurrency` group keyed on `client_payload.image_name`, but the caller-side
group protects against overlapping *workflow runs* triggered in quick
succession.

`id-token: write` is required of every caller — see
[Syncing to ECR via OIDC](syncing.md#syncing-to-ecr-via-oidc) for why, and for
the alternative if that trade isn't acceptable.

??? abstract "`bump_images.yml` Reference"

    ::: .github/workflows/bump-images.yml
        handler: github

    Follow-up jobs in the calling workflow can consume the outputs, e.g.:

    ```yaml
    jobs:
      bump:
        uses: gulfofmaine/odp-releaser/.github/workflows/bump-images.yml@<sha-or-tag>

      report:
        needs: [bump]
        if: needs.bump.outputs.changed == 'true'
        runs-on: ubuntu-latest
        steps:
          - env:
              IMAGE_NAME: ${{ needs.bump.outputs.image_name }}
              DIGEST: ${{ needs.bump.outputs.digest }}
            run: echo "Bumped $IMAGE_NAME to $DIGEST"
    ```

    To insert steps *between* the bump and the commit/PR (e.g. syncing the image to
    another registry yourself), use the
    [`bump_images` composite action](../api/actions.md#bump_images) with
    `stage_only: "true"` instead of this workflow.

## `commit` vs `pull_request`

Each image in [`.github/image_manifest.yaml`](image_manifest.md) sets
`update_mode: commit` (default) or `update_mode: pull_request` per
`ImageConfig`. In `commit` mode the workflow pushes the manifest edits straight
to the checked-out branch (normally the default branch); in `pull_request` mode
it opens (or updates) a pull request on a stable branch named
`odp-releaser/bump-<image_name>` via `peter-evans/create-pull-request`.

### Requesting reviewers on bump pull requests

`pull_request`-mode bumps can request reviews: set `reviewers` (GitHub
usernames) and/or `team_reviewers` (team slugs, no org prefix as they need to be in the parent org of the deployment repo) — per image
config, or under `defaults:` to apply to every config. A config's own list
replaces the default (an explicit `[]` requests none); when several matching
configs disagree, the first in config order wins with a warning, mirroring how
`environment` resolves.

Requesting a **team** review needs a token with organization "Members: read" (the default `GITHUB_TOKEN` can't do it). The workflow handles this automatically:
when the checked-out image manifest contains a `team_reviewers` key, the
`ci_app_id` app token is minted with that permission added (grant the app the
organization "Members: read" permission first; without `ci_app_*` secrets team
reviews can't be requested). One upstream caveat from
`peter-evans/create-pull-request`: a requested reviewer who is the PR's author
causes the request-review call to fail.

!!! warning "The `ci_app_*` PR-CI-triggering note"

    GitHub Actions deliberately does not trigger further workflow runs from a commit
    or pull request authored with the default `GITHUB_TOKEN`. If any of your images
    use `update_mode: pull_request`, that means your own CI would never run against
    the bump PR unless the commit/PR is authored with a GitHub App token instead.
    Passing `ci_app_id` / `ci_app_private_key` — your deploy org's own dispatch app
    credentials — makes the workflow mint that token before checkout, so the pushed
    commit and/or opened PR is authored by your app and does trigger CI. See
    [GitHub App](github_app.md#5-reusing-the-dispatch-app-to-trigger-your-own-ci) for
    how to obtain and wire those credentials.

## Reporting deployments back to the source repo

When the `reporter_app_id` / `reporter_app_private_key` (or `reporter_apps`)
secrets are set, the
[`report_deployment` composite action](../api/actions.md#report_deployment) runs
after a successful bump. It creates a
[GitHub deployment](https://docs.github.com/en/rest/deployments/deployments) on
the **source** repository at the commit that built the image
(`client_payload.git_sha`) and sets its status, so the source repo's pull
request timeline and Environments sidebar show where the image went.

- The deployment **state** mirrors what happened on the deploy side:
    - `success`
  when the bump was committed directly
    - `queued` when a bump pull request was
  opened but not yet merged — call [`report-merged.yml`](report_merged.md) from
  the deploy repo to flip it to `success` once the bump PR merges.

    !!! note

        This records that the manifest change landed — whether ArgoCD has synced
        it to a cluster is downstream of this tool.

- The **environment name** defaults to the deploy repo's `owner/name` slug; set
  `environment` in [`.github/image_manifest.yaml`](image_manifest.md) — per
  image config, or under `defaults:` as a repo-wide default — to override it.
- The **"View deployment" link** defaults to the bump commit (`commit` mode) or
  the bump pull request (`pull_request` mode); set `environment_url` in the
  image manifest config — again per image config or under `defaults:` — to point
  it at the running app instead (templated with `{new_tag}`, `{git_sha}`, and
  `{digest}`). The logs link points at the bump workflow run.
- Reporting is **best-effort**: the step runs with `continue-on-error`, so a
  failed report never fails the bump itself.

The credentials belong to a **reporter app** with `Deployments: Read and write`
installed on the source repos — normally a single app owned by the deploy org
and installed by each source org. See
[GitHub App](github_app.md#the-reporter-app) for how to set one up.

## Commenting on the source pull request

If that same reporter app has additionally been granted
`Pull requests: Read and write`, the
[`comment_on_pr` composite action](../api/actions.md#comment_on_pr) runs after
the deployment report and posts a comment on the source pull request naming the
image, the tag and the environment in words — the readable counterpart to the
deployment record.

- The **state** decides which template is used:
    - `staged` for a
  `pull_request`-mode bump still awaiting review
    - `deployed` for one that has landed. Calling [`report-merged.yml`](report_merged.md) rewrites the staged comment as deployed when the bump PR merges.
- The **templates** come from the image manifest config, inherited field by
  field from `defaults:` and then the built-ins — see
  [Pull request comments](image_manifest.md#pull-request-comments).
- The step is **skipped entirely** when commenting is disabled for the image, or
  when the event carried no source pull request (only `push` events do), and is
  otherwise **best-effort** like the deployment report.
- The comment token is minted **separately** from the deployment one, with
  `pull_requests: write` only, so a source org that hasn't accepted the comment
  permission keeps receiving deployment reports.

## Allowed source repos and actors

Every dispatch carries `client_payload.repo` — the source repo's `owner/name`
slug — and `client_payload.source.actor` — the GitHub user who triggered the
source build — as its identifiers for "who sent this" (see
[Client Payload](../api/client_payload.md)). A deploy repo's
`.github/image_manifest.yaml` can restrict both per `ImageConfig`:

- `allowed_source_repos`: trusted `owner/name` slugs.
- `allowed_actors`: a mapping with `users` (GitHub usernames, compared
  case-insensitively) and/or `teams` (`org/team-slug` entries). Teams live in
  the source orgs, so membership is checked with the same
  [reporter app](github_app.md#the-reporter-app) credentials (`reporter_apps` /
  `reporter_app_id` / `reporter_app_private_key`) that deployment reporting
  uses — grant the reporter app the organization "Members: read" permission for
  this.

Both can also be set under `defaults:` to apply to every config; a config's own
value replaces the default entirely (an empty list denies everyone). Leaving a
resolved value unset disables that check.

A config whose allowlists reject the payload is skipped with a warning, so other
configs for the same image can still apply — e.g. anyone may bump a dev overlay
while only release managers reach production. When every event-matched config
for the image rejects the payload, `bump-images` fails (non-zero exit, no
manifest changes) so unauthorized attempts are loud.

To share allowlists between configs, use YAML anchors and merge keys — top-level
`x-` keys are ignored by the schema:

```yaml
x-prod-guards: &prod-guards
  allowed_source_repos: [gulfofmaine/Neracoos-1-Buoy-App]
  allowed_actors:
    users: [abkfenris]
    teams: [gulfofmaine/deployers]

images:
  gmri/neracoos-mariners-dashboard:
    - <<: *prod-guards
      events: [release]
      kustomize_manifests:
        - ../apps/mariners/kustomization.yaml
```

This is the deploy repo's own defense-in-depth check, independent of which
source orgs the deploy org's dispatch app trusts — see
[Image manifest](image_manifest.md) for the fields and
[GitHub Apps](../api/github_apps.md) for the credential-level trust boundary.
