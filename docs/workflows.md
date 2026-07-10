---
icon: lucide/workflow
---

# Reusable Workflows

This repo has workflows that can be used in other repos.

- `notify` should be run in the code repo and triggered in a workflow after an image has been built and pushed to a public repository.
- `bump_images` can be incorperated into a `repository_dispatch` workflow to apply the image to deployment manifests.

Both will be called with the [cross-repo `uses:` syntax](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#calling-a-reusable-workflow).

## Notify

Configure the `if:` to constrain to the correct events/repo/branch.

`secrets: inherit` needs to be set so that it can use the Github token.

### `with:` Inputs:

- `image_name`
- `image_tag`
- `image_digest`
- `environment`

### Environment variables

- `DISPATCH_APP_ID`
- `DISPATCH_APP_PRIVATE_KEY`

```yaml
    notify:
        needs: [shortsha, build_test_push]
        if: ${{ github.repository == 'ioos/buoy_retriever' && github.event_name != 'pull_request' }}
        uses: gulfofmaine/odp-dispatch/.github/workflows/_notify.yml@<sha-or-tag>
        permissions:
            contents: read
            pull-requests: read
        secrets: inherit
        with:
            image_name: buoy_retriever_hohonu
            image_tag: ${{ needs.shortsha.outputs.shortsha }}
            image_digest: ${{ needs.build_test_push.outputs.image_digest }}
```

## Bump Images

