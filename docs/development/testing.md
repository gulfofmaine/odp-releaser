---
icon: lucide/flask-conical
---

# Testing

`odp-releaser` can exercise both config files against canned client payloads
without dispatching anything, minting a token, or making a single network call.
That is the fastest way to answer "would this config actually do what I think?"
before a real release proves otherwise.

For the static checks — schema, unknown keys, bad selectors, bad templates — see
[validating deploy targets](../source/deploy_targets.md#validating) and
[validating an image manifest](../deploy/image_manifest.md#validating) instead.
The `test` commands here answer a different question: given a realistic payload,
what happens?

## Testing a deploy targets config

`odp-releaser test notify` builds a canned `client_payload` and reports, per
configured target, whether dispatch app credentials are available — without
minting any tokens or making any network calls.

```bash
$ odp-releaser test notify --image-name gmri/neracoos-mariners-dashboard --event-type push
```

It exits non-zero if the deploy targets file is missing or fails to parse; an
existing file that is empty or contains an empty array is a valid no-op.

## Testing an image manifest config

`odp-releaser test bump-images` runs a bump against one of the canned payloads
and shows what it would write.

```bash
$ odp-releaser test bump-images
```

The payloads it draws from are the same ones documented under
[Client Payload](../api/client_payload.md) — one per supported event type
(`push`, `release`, `workflow_dispatch`).

## Testing aids in the workflows

The reusable workflows carry a few inputs that exist purely so they can be
exercised without side effects. They are documented with everything else in each
workflow's reference table, and grouped there as "Testing aids":

- [`notify.yml`](../source/notify.md#reference) — `dry_run` resolves dispatch
  credentials for every target but sends nothing; `event_name` overrides the
  event the client payload is built from (`workflow_dispatch` is the simplest
  override, since it needs no event file, token, or pull request lookup).
- [`bump-images.yml`](../deploy/bump_images.md) — `client_payload`
  supplies a payload explicitly instead of reading the triggering event's;
  `dry_run` runs the CLI with `--dry-run` and skips the commit and pull-request
  steps, while still producing every output.

## Self-testing (e2e CI)

This repo's own CI (`.github/workflows/ci.yml`) exercises both reusable
workflows end-to-end on every pull request, using exactly those inputs:

- `e2e-notify` calls `notify.yml` with `dry_run: true`,
  `event_name: workflow_dispatch`, dummy dispatch credentials, and the fixture
  targets in `tests/e2e/deploy_targets.yaml`. Credentials are resolved for every
  target but nothing is dispatched; the job fails if any target's credentials
  can't be resolved, and its `results`/`target_count` outputs are asserted
  downstream.
- `e2e-payload` installs the CLI from the PR's checkout and generates real
  client payloads with `odp-releaser test make-payload`.
- `e2e-bump-commit` / `e2e-bump-pr` call `bump-images.yml` with those payloads,
  `dry_run: true`, and the fixture config in `tests/e2e/image_manifest.yaml` (one
  image per update mode, covering both kustomize pin styles).
- `e2e-assert` checks the workflows' outputs: notify's `results` (both targets
  attempted, all ok, dry-run detail) and `target_count`, plus bump-images'
  `image_name`, `digest`, `changed`, `update_mode`, `commit_message`, `pr_title`,
  and `branch_name`.

Because the reusable workflows are called locally (`uses: ./.github/...`), each
PR run also proves the real production path against the PR's own commit: the
`$/` references, both [composite actions](../api/actions.md), and the CLI they
install. `e2e-action-self-install` covers the case they miss — a bare
`bump_images` with no install step, so the action must install the CLI itself.
