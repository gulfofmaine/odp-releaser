---
icon: lucide/git-merge
---

# Report merged workflow

Runs in the **deploy** repo when a pull request closes. A `pull_request`-mode
bump reports its deployment to the source repo as `queued` — nothing is live
until the bump PR merges. This workflow un-queues it: it reads the report
metadata that [`bump-images`](bump_images.md) embedded in the PR body (an
invisible HTML comment carrying the client payload, environment, environment
URL, and the resolved comment templates), finds the queued deployment on the
source repo for the same commit and environment, and flips its status to
`success`. It then rewrites the source pull request's `staged` comment as
`deployed`, if commenting is configured.

Everything it needs travels in that PR body, which is why this workflow never
checks out the deploy repo and never reads the image manifest.

Deploy repos whose images all use `update_mode: commit` don't need this
workflow — commit-mode bumps report `success` and comment `deployed`
immediately.

One gap worth knowing: a bump pull request **closed without merging** leaves its
`staged` comment (and `queued` deployment) as they are, since this workflow only
ever runs for merged PRs.

## Caller example

```yaml
on:
  pull_request:
    types: [closed]

jobs:
  report:
    if: >-
      github.event.pull_request.merged == true &&
      startsWith(github.event.pull_request.head.ref, 'odp-releaser/')
    uses: gulfofmaine/odp-releaser/.github/workflows/report-merged.yml@<sha-or-tag>
    secrets:
      reporter_app_id: ${{ secrets.REPORTER_APP_ID }}
      reporter_app_private_key: ${{ secrets.REPORTER_APP_PRIVATE_KEY }}
      # reporter_apps: ${{ secrets.REPORTER_APPS }}  # optional multi-org
```

The `if:` gate matches merged pull requests on the stable
`odp-releaser/bump-<image_name>` branches that `bump-images` uses. A PR without
embedded odp-releaser metadata is a friendly no-op (the job logs "nothing to
report" and succeeds), so a broader gate is safe — the branch prefix check just
avoids spinning up jobs for unrelated PRs.

If a merged bump PR's report is ever missed (e.g. the secrets weren't configured
yet), re-running is safe: reporting is idempotent, reusing the existing
deployment for the same commit + environment rather than creating duplicates.

## Reference

::: .github/workflows/report-merged.yml
    handler: github

The credentials are the same reporter app credentials
[`bump-images`](bump_images.md#reporting-deployments-back-to-the-source-repo)
uses — see [GitHub App](github_app.md#the-reporter-app).
