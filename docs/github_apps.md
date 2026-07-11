---
icon: lucide/key-round
---

# GitHub Apps

`odp-releaser` moves container images across an organizational trust
boundary: a public **source** repo (which built and pushed an image) tells a
private **deploy** repo (which owns the Kubernetes/Helm manifests) that a new
image is ready. That message travels as a
[`repository_dispatch`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#repository_dispatch)
event, and sending one requires a token with `contents: write` on the
**target** repository — the default `GITHUB_TOKEN` a workflow run gets is
scoped only to the repo the workflow is running in, so it can never reach
across repos, let alone across orgs.

GitHub Apps close that gap. Each deploy org owns and runs its own dispatch
app, installs it only on its own deploy repos, and hands out a private key to
each source org it trusts. That ownership model buys a real trust boundary:

- A deploy org can revoke a single source org's access (delete that org's
  key) without touching any other source org's ability to dispatch.
- No org ever holds a credential that reaches into another org's
  repositories — a compromised source-org secret only exposes the deploy
  repos that org was explicitly trusted with.
- Installing the app only on deploy repos (never source repos) keeps the
  blast radius of a leaked app private key limited to `contents`/PR-write on
  a known, small repo list.

The rest of this page covers the two sides of that relationship, the token
flow the CLI runs on every dispatch, and a reserved v2 concept.

## For deploy org admins

### 1. Create the app

In your deploy org's settings, go to **Settings → Developer settings →
GitHub Apps → New GitHub App** (org-level, not a personal app) and configure:

- **Permissions**: Repository permissions → `Contents: Read and write` and
  `Pull requests: Read and write`. Nothing else is required — the app never
  needs issues, actions, or admin permissions.
- **Webhooks**: turn the "Active" toggle off. This app is only ever used to
  mint tokens on demand; it doesn't need to receive events.
- **Where can this app be installed?**: "Only on this account" is sufficient
  unless you have a reason to allow installs elsewhere.
- **Name**: suggested pattern `<org>-odp-dispatch`, e.g.
  `gulfofmaine-odp-dispatch`. One app per deploy org keeps the "who can
  dispatch into my repos" question answerable at a glance.

After creating the app, note its **App ID** (shown on the app's settings
page) — you'll share it alongside each private key.

### 2. Install it on your deploy repos

From the app's settings page, **Install App**, and select only the
repositories that should receive dispatch events (or "All repositories" if
that matches your org's posture). The app must be installed on a repo before
any source org can dispatch to it — an install-less target fails with the
"app not installed" error described below.

### 3. Generate one private key per trusted source org

Under the app's **Private keys** section, click **Generate a private key**
once per source org you trust, and keep track of which `.pem` belongs to
which org. Share the app ID and that org's private key with the source org's
maintainers (a password manager or secrets-sharing tool, not email/Slack in
plaintext).

Using a distinct key per source org — rather than the same key for everyone —
is what makes revocation surgical: deleting one key only breaks dispatches
from the org it was given to.

### 4. Rotation and revocation

- **Revoke a source org**: delete that org's private key from the app's
  settings page. Their next dispatch attempt fails with "no credentials for
  owner" once they've removed the stale secret, or with an authentication
  error immediately since GitHub invalidates JWTs signed with a deleted key.
- **Rotate a key**: generate a new one, share it with the source org, have
  them update their secret, then delete the old key.
- **Installation access tokens need no separate revocation.** They're minted
  per dispatch, live for one hour, and are scoped to a single repository —
  the CLI never persists them, so there's nothing sitting around to leak.

### 5. Wire your own app into `bump-images.yml` (PR-mode CI trigger)

If any `ImageConfig` in your manifest config uses `update_mode: pull_request`
(see [Image manifest config](image_manifest_config.md)), the bump workflow
opens a pull request instead of committing directly. A PR (or push) made with
the default `GITHUB_TOKEN` does **not** trigger further workflow runs — this
is a deliberate GitHub Actions anti-recursion rule — so your repo's own CI
would never run against the bump PR unless you supply an app-minted token
instead.

Since your deploy org already owns a dispatch app with `contents: write` +
`pull-requests: write`, you can reuse that same app's credentials for this:
pass its App ID and private key to `bump-images.yml` as the `ci_app_id` /
`ci_app_private_key` secrets. When set, the workflow mints a token with that
app before checkout, and both the commit-and-push and the pull-request paths
use it instead of `GITHUB_TOKEN` — so the resulting commit/PR is authored by
your app and does trigger your CI. See
[Bump images](workflows.md#bump-images) for the full input/secret list.

## For source repo maintainers

Your repo's `notify` job needs credentials for every deploy org it dispatches
to. Store these as GitHub Actions secrets (org-level secrets scoped to your
source repos work well if many repos in your org call `notify`):

- **Single deploy org** (the common case): store the deploy org's app ID and
  private key as `DISPATCH_APP_ID` and `DISPATCH_APP_PRIVATE_KEY`. These are
  used as the default credentials for any dispatch target.
- **Multiple deploy orgs**: store a `DISPATCH_APPS` secret — a JSON object
  mapping each target owner to its own `{app_id, private_key}`:

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
  `DISPATCH_APP_PRIVATE_KEY` pair when the owner isn't in the mapping. You
  only need `DISPATCH_APPS` for owners that aren't covered by the default
  pair.

### Failure modes and fixes

Every target is attempted independently and reported in the job's step
summary, so one bad target never blocks the others. The two errors you'll
see:

- **"No dispatch app credentials for owner `X`."** — Neither `DISPATCH_APPS`
  nor the default `DISPATCH_APP_ID`/`DISPATCH_APP_PRIVATE_KEY` pair covers
  that owner. Fix: add an entry for `X` to `DISPATCH_APPS`, or confirm the
  default pair is meant to cover `X`.
- **"The dispatch app is not installed on `X/Y`."** — Credentials resolved
  fine, but the deploy org's app isn't installed on that specific repo. Fix:
  ask the deploy org admin to install their app on `X/Y` (step 2 above).

## Token flow

For every deploy target, `odp-releaser notify` (via
`odp_releaser.github.send_dispatch`) runs the same sequence:

1. **Resolve credentials** for `target.owner` (`DISPATCH_APPS` mapping, then
   the default pair) — raises `MissingCredentialsError` if neither covers it.
2. **Authenticate as the App** with a JWT signed by the resolved private key.
3. **`GET /repos/{owner}/{repo}/installation`** to find the app's
   installation on the target repo — a 404 here raises
   `AppNotInstalledError`.
4. **Mint an installation access token** scoped to `repositories: [repo]`
   with `permissions: {contents: write}` — nothing broader than the single
   target repo, no matter what the app's own maximum permissions are.
5. **`POST /repos/{owner}/{repo}/dispatches`** with that token, sending
   `event_type` and the `client_payload` (see
   [Client Payload](client_payload.md)).

These tokens are short-lived (1 hour), created fresh for each target on each
run, and are never logged or written to disk — only exception messages,
`owner`/`repo`/`event_type`, and (at debug level) the client payload itself
are logged.

## Future: commenter apps (v2)

The dispatch app role only ever pushes information one direction: source →
deploy. A reserved, symmetric **commenter app** role is planned for a future
release — owned by the *source* org instead, installed on source repos with
`pull-requests: write`, so a deploy repo could report back onto the source
pull request where an image ended up being deployed. The seam for this
already exists in the codebase
(`odp_releaser.github.upsert_pr_comment`), but it currently raises
`NotImplementedError` — `comment-on-pr` is not implemented in this release.
