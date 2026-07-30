---
icon: lucide/key-round
---

# GitHub Apps

A source repo never *owns* a GitHub App in this setup. It holds credentials
handed to it by each deploy org it dispatches into, and it optionally consents
to a deploy org's reporter app being installed on it. The trust model behind
both is in [GitHub Apps](../api/github_apps.md).

## Dispatch credentials

Your repo's [`notify` job](notify.md) needs credentials for every deploy org it
dispatches to. Store these as GitHub Actions secrets (org-level secrets scoped
to your source repos work well if many repos in your org call `notify`):

- **Single deploy org** (the common case): store the deploy org's app ID and
  private key as `DISPATCH_APP_ID` and `DISPATCH_APP_PRIVATE_KEY`. These are
  used as the default credentials for any dispatch target.
- **Multiple deploy orgs**: store a `DISPATCH_APPS` secret - a JSON object
  mapping each target org/owner to its own `{app_id, private_key}`:

  ```json
  {
    "gulfofmaine": {
      "app_id": "123456",
      "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
    },
    "ioos": {
      "app_id": "234567",
      "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
    }
  }
  ```

  For each target, `odp-releaser notify` looks up `target.owner` in
  `DISPATCH_APPS` first, falling back to the `DISPATCH_APP_ID` /
  `DISPATCH_APP_PRIVATE_KEY` pair when the owner isn't in the mapping. You only
  need `DISPATCH_APPS` for owners that aren't covered by the default pair.

??? example "Assembling `DISPATCH_APPS` from one secret per deploy org"

    Keeping every credential in one hand-built JSON blob means re-editing a
    document full of escaped newlines every time a single deploy org rotates its
    key. You can instead store each org's credentials as its own pair of
    secrets, and assemble the mapping in the caller's `secrets:` block:

    ```yaml
    jobs:
      notify:
        uses: gulfofmaine/odp-releaser/.github/workflows/notify.yml@<sha>
        permissions:
          contents: read
          pull-requests: read
        with:
          image_name: ghcr.io/ioos/buoy_retriever_hohonu
          tag: ${{ needs.shortsha.outputs.shortsha }}
          digest: ${{ needs.build_test_push.outputs.image_digest }}
        secrets:
          dispatch_apps: >-
            {
              "gulfofmaine": {
                "app_id": "${{ secrets.GULFOFMAINE_DISPATCH_APP_ID }}",
                "private_key": ${{ toJSON(secrets.GULFOFMAINE_DISPATCH_APP_PRIVATE_KEY) }}
              },
              "ioos": {
                "app_id": "${{ secrets.IOOS_DISPATCH_APP_ID }}",
                "private_key": ${{ toJSON(secrets.IOOS_DISPATCH_APP_PRIVATE_KEY) }}
              }
            }
    ```

    - **`toJSON()` around each private key, with no surrounding quotes.** A PEM
      contains real newlines, so interpolating it as `"${{ secrets.KEY }}"`
      produces invalid JSON. `toJSON()` emits the value already quoted and
      escaped (`\n`), which `odp-releaser` then parses back into the original
      key. The app IDs are plain and stay in ordinary quotes.
    - **The folded `>-` scalar.** It lets the JSON stay readable across several
      lines instead of one long one. YAML keeps the line breaks of the
      more-indented lines and strips the trailing newline, and JSON ignores
      whitespace between tokens, so only the JSON structure has to be right.

    Expressions are evaluated in `jobs.<job_id>.secrets.<name>`, which is why no
    intermediate step is needed. A job output is
    [redacted when it contains a secret](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax-for-github-actions#jobsjob_idoutputs),
    so assembling the JSON in a `run:` step of an earlier job and passing it on
    would not survive the trip.

    !!! warning "The assembled string is not covered by GitHub's log masking"

        GitHub masks a secret's *literal* value. The JSON-escaped form is a
        different string, so it would not be redacted if something echoed it.
        `odp-releaser` never logs credentials, but don't add a debugging `echo`
        of this value.

Ask the deploy org's admins for the app ID and a private key — they generate one
key per source org they trust, so that revoking yours doesn't affect anyone
else. Share it through a password manager or secrets-sharing tool, not
email/Slack in plaintext.

### Failure modes and fixes

Every target is attempted independently and reported in the job's step summary,
so one bad target never blocks the others. The two errors you'll see:

- **"No dispatch app credentials for owner `X`."** — Neither `DISPATCH_APPS` nor
  the default `DISPATCH_APP_ID`/`DISPATCH_APP_PRIVATE_KEY` pair covers that
  owner. Fix: add an entry for owner `X` to `DISPATCH_APPS`, or confirm the default
  pair is meant to cover `X`.
- **"The dispatch app is not installed on `X/Y`."** — Credentials resolved fine,
  but the deploy org's app isn't installed on that specific repo. Fix: ask the
  deploy org admin to install their app on `X/Y`.

## Receiving deployment reports

A deploy repo can report each bump back onto this repo, so its pull request
timeline shows "deployed to *environment*" entries and its Environments sidebar
shows the latest deployed commit per deploy repo. That needs the deploy org's
**reporter app** installed here.

Install it on the source repos that should receive reports — repo **Settings**
won't show it, so use the app's public page / install link the deploy org
shares. Before consenting, you can verify on that page exactly what the app
requests:

- `Deployments: Read and write` alone if it only records deployments;
- plus `Pull requests: Read and write` if it also comments on your pull
  requests — which additionally lets it edit pull request titles, bodies,
  labels, and reviewers. It cannot merge or push code (that needs `Contents`),
  but it *is* a wider grant than a deployment record, and leaving it off is a
  perfectly good answer: everything else keeps working.

To revoke a deploy org's ability to report, uninstall its app.

Two caveats worth knowing before installing:

- Creating a deployment fires a `deployment` webhook/Actions event in this repo.
  That's harmless unless a workflow here triggers `on: deployment` — check
  first if any do.
- Comments fire `issue_comment`, the same class of caveat. Harmless unless a
  workflow triggers `on: issue_comment`.

Only `push` events can be commented on: the comment lands on the pull request
associated with the commit that built the image, which `odp-releaser notify`
only resolves for `push` events. Release and `workflow_dispatch` dispatches have
no pull request, and the comment step is skipped for them.

If your org would rather own the reporter app itself and hand keys out (the
mirror image of the dispatch model) that's supported too; see
[Alternative: source-org-owned reporter apps](../api/github_apps.md#alternative-source-org-owned-reporter-apps).
