---
icon: lucide/server
---

# Deployment repo

A **deployment repo** owns the Kubernetes, Kustomize, and/or Helm manifests, or other YAML/JSON deployment configurations that
reference container images. Its job in odp-releaser is to react: when a source
repo dispatches "this image is now at this tag and digest", the deploy repo
decides — from its own config — which manifests to edit, whether the change goes
straight in or through review, and whether to report the result back.

Everything it does is driven by
[`.github/image_manifest.yaml`](image_manifest.md) in its own checkout. A source
repo can only *ask*; the deploy repo's config decides what actually happens, and
whether that source repo was even allowed to ask.

It owns:

1. a [`bump-images`](bump_images.md) workflow, triggered by
   `repository_dispatch`;
2. a [`report-merged`](report_merged.md) workflow, if any image opens pull
   requests rather than committing directly;
3. [`.github/image_manifest.yaml`](image_manifest.md), the config for both;
4. [the GitHub Apps](github_app.md) — the deploy org owns the dispatch app that
   source repos use to reach in, and normally the reporter app that reaches back
   out.

## Minimum setup

**`.github/image_manifest.yaml`** — what to update when an image arrives:

```yaml
images:
  ghcr.io/ioos/buoy_retriever_hohonu:
    - events: [push]
      kustomize_manifests:
        - ./apps/buoy-retriever/kustomization.yaml
```

**A `bump-images` calling workflow**, listening for the dispatch:

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
```

That is enough to have a dispatched image bump a Kustomize manifest and commit
it. See [Bump images](bump_images.md) for what the optional inputs and secrets
allows configuring.

**A `report-merged` calling workflow**, if some image uses
`update_mode: pull_request`:

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
```

Deploy repos whose images all use `update_mode: commit` don't need it —
commit-mode bumps report `success` and comment `deployed` immediately.

## Optionally

- **[Report back to the source repo](bump_images.md#reporting-deployments-back-to-the-source-repo)**
  with a [reporter app](github_app.md#the-reporter-app), so a bump shows on the
  source pull request's timeline — and, with one more permission, as a comment.
- **[Trigger your own CI on bump pull requests](bump_images.md#commit-vs-pull_request)**
  by passing `ci_app_*` secrets; a PR authored with `GITHUB_TOKEN` deliberately
  triggers nothing.
- **[Deploy from a mirrored registry](syncing.md)** with `deployed_as`, and have
  odp-releaser push the image there with `sync: true`.
- **[Restrict who may bump what](bump_images.md#allowed-source-repos-and-actors)**
  with `allowed_source_repos` and `allowed_actors`, per image config.
