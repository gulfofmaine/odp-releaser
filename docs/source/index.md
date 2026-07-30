---
icon: lucide/package
---

# Source repo

A **source repo** builds and pushes a container image. Its job in odp-releaser
is to announce that: after the image lands in a registry, it sends a
`repository_dispatch` event to every deploy repo that cares, carrying the image
name, the new tag, the digest, and enough provenance for the deploy side to
decide what to do with it.

It owns three things:

1. a [`notify` job](notify.md) in the workflow that builds the image;
2. a [`.github/deploy_targets.yaml`](deploy_targets.md) listing where to
   dispatch;
3. [dispatch credentials](github_app.md) — an app ID and private key handed to
   it by each deploy org it dispatches into.

Nothing else in the source repo changes: odp-releaser never edits its files and
never pushes to it.

## Minimum setup

**`.github/deploy_targets.yaml`** — what deployment repos to notify:

```yaml
- owner: gulfofmaine
  repo: some-deploy-repo
```

**Secrets** — `DISPATCH_APP_ID` and `DISPATCH_APP_PRIVATE_KEY`, from the deploy
org that owns the repo above. Org-level secrets scoped to your source repos work
well if several repos in your org call `notify`.

**A `notify` job**, after the build job that pushed the image:

```yaml
jobs:
  notify:
    needs: [build_test_push]
    if: ${{ github.repository == 'ioos/buoy_retriever' && github.event_name != 'pull_request' }}
    uses: gulfofmaine/odp-releaser/.github/workflows/notify.yml@<sha-or-tag>
    permissions:
      contents: read
      pull-requests: read
    with:
      image_name: ghcr.io/ioos/buoy_retriever_hohonu
      tag: ${{ needs.build_test_push.outputs.tag }}
      digest: ${{ needs.build_test_push.outputs.image_digest }}
    secrets:
      dispatch_app_id: ${{ secrets.DISPATCH_APP_ID }}
      dispatch_app_private_key: ${{ secrets.DISPATCH_APP_PRIVATE_KEY }}
```

See [Security notes](../getting-started.md#security-notes) for the `if:` gate.

After the dispatch, it is the
[deploy repo's](../deploy/index.md) responsibility.

## Optionally

- **Install the deploy org's [reporter app](github_app.md#receiving-deployment-reports)**
  on this repo, so bumps show up on its pull request timeline and Environments
  sidebar — and, if the app has the permission, as a comment on the pull request
  that built the image.
- **Validate `deploy_targets.yaml` in CI or pre-commit**, so a typo'd owner is
  caught before a release rather than during one. See
  [Pre-commit hooks](../getting-started.md#pre-commit-hooks).
