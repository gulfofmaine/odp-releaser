---
icon: lucide/megaphone
---

# Notify workflow

Runs in the **source** repo, after an image has been built and pushed. It builds
a `client_payload` describing the image and the commit that produced it, reads
[`.github/deploy_targets.yaml`](deploy_targets.md), and sends one
`repository_dispatch` event per target — each authenticated with a fresh
installation token scoped to that single repository.

Every target is attempted independently and reported in the job's step summary,
so one bad target never blocks the others.

## Caller example

```yaml
jobs:
  notify:
    needs: [shortsha, build_test_push]
    if: ${{ github.repository == 'ioos/buoy_retriever' && github.event_name != 'pull_request' }}
    uses: gulfofmaine/odp-releaser/.github/workflows/notify.yml@<sha>
    permissions:
      contents: read
      pull-requests: read
    with:
      image_name: ghcr.io/ioos/buoy_retriever_hohonu
      tag: ${{ needs.shortsha.outputs.shortsha }}
      digest: ${{ needs.build_test_push.outputs.image_digest }}
      # environment: production                           # optional gate
      # deploy_targets_path: .github/deploy_targets.yaml  # optional
      # verbosity: 1                                       # optional, default
    secrets:
      dispatch_app_id: ${{ secrets.DISPATCH_APP_ID }}
      dispatch_app_private_key: ${{ secrets.DISPATCH_APP_PRIVATE_KEY }}
      # dispatch_apps: ${{ secrets.DISPATCH_APPS }}        # optional multi-org
```

Note the explicit `secrets:` block — `notify.yml` does **not** support
`secrets: inherit`, since it only ever needs the dispatch credentials named
above.

Add an `if:` to constrain the job to the right repo, branch, and event so forks
and unrelated pushes don't dispatch anything — see
[Security notes](../getting-started.md#security-notes).

## The protected `environment` gate

Setting `with: environment:` runs the job under that
[GitHub environment](https://docs.github.com/en/actions/how-tos/deploy/manage-environments/manage-environments-for-deployment),
so any protection rules configured there (required reviewers, wait timers,
branch restrictions) apply before a single dispatch is sent. Leave it empty to
skip gating entirely.

## Reference

::: .github/workflows/notify.yml
    handler: github

Where the dispatch credentials come from, and how to request them from a deploy
org, is covered in [GitHub App](github_app.md).
