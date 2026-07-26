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
flow the CLI runs on every dispatch, and the symmetric **reporter app** that
lets a deploy repo report deployments — and optionally pull request comments —
back onto the source repo.

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
(see [Image manifest config](config/image_manifest.md)), the bump workflow
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

## Reporter apps

The dispatch app role only ever pushes information one direction: source →
deploy. The symmetric **reporter app** role closes the loop in the other
direction: after `bump-images` lands a manifest change, the deploy repo can
report a [GitHub deployment](https://docs.github.com/en/rest/deployments/deployments)
back onto the source repository at the commit that built the image. The
source repo's pull request timeline then shows "deployed to *environment*"
entries, and its Environments sidebar shows the latest deployed commit per
deploy repo — with no comment formatting or notification plumbing.

Unlike the dispatch role — where per-source-org keys guard a powerful
`contents: write` grant — the reporter only ever needs `deployments: write`,
a low-stakes permission that can create deployment records but never touch
code. That makes a much simpler ownership model the recommended default:
**one app, owned by the deploy org, installed by each source org that wants
reports**. No private key is ever shared; installing the app *is* the
consent, and uninstalling it *is* the revocation.

### For deploy org admins (recommended: one shared reporter app)

Create the app in your deploy org's settings, like the dispatch app but
with:

- **Permissions**: Repository permissions → `Deployments: Read and write`.
  Add `Pull requests: Read and write` only if you want bumps to comment on the
  source pull request as well (see [Pull request comments](#pull-request-comments)
  — it's a wider grant, and existing installations have to accept it). Add
  Organization permissions → `Members: Read-only` only if your deploy
  repos use `allowed_actors` team entries in their image manifests — the
  membership check runs with this app's credentials, since it's the one
  installed where the teams live. Nothing else.
- **Webhooks**: off, same as the dispatch app.
- **Where can this app be installed?**: **"Any account"** — source orgs must
  be able to install it on their own repos.
- **Name**: suggested pattern `<org>-odp-reporter`, e.g.
  `gulfofmaine-odp-reporter`.

Generate a single private key and store it — together with the App ID — as
GitHub Actions secrets in your deploy repos (or org):
`REPORTER_APP_ID` / `REPORTER_APP_PRIVATE_KEY`. The key never leaves your
org. Then share the app's public install link with each source org.

Pass the secrets to `bump-images.yml` and `report-merged.yml` as the
`reporter_app_id` / `reporter_app_private_key` secrets — see
[Bump images](workflows.md#bump-images) and
[Report merged](workflows.md#report-merged). In `bump-images.yml` reporting
is best-effort: a failed report never fails the bump itself.

### For source repo maintainers

Install the deploy org's reporter app on the source repos that should
receive deployment reports (repo **Settings** won't show it — use the app's
public page / install link the deploy org shares). Before consenting,
you can verify on that page exactly what the app requests: `Deployments: Read
and write` alone if it only records deployments, plus `Pull requests: Read and
write` if it also comments on your pull requests — which additionally lets it
edit pull request titles, bodies, labels and reviewers. To revoke a deploy
org's ability to report, uninstall its app — no key coordination needed.

One caveat: creating a deployment fires a `deployment` webhook/Actions event
in the source repo. That's harmless unless a source workflow triggers `on:
deployment` — check before installing if your source repos have such
workflows.

### Alternative: source-org-owned reporter apps

Orgs that prefer the dispatch model's tighter blast-radius can mirror it
instead: the *source* org owns the reporter app, installs it on its own
repos, and hands a private key to each deploy org it wants reports from
(one key per deploy org, revocable individually). The tradeoff is real key
sharing and rotation work — justified when a leaked deploy-org secret
reaching *all* installed source repos' deployments is a concern, less so
given the permission can't modify code.

The CLI supports both models with the same secrets: per-source-owner
credentials go in a `REPORTER_APPS` JSON object (mapping each source owner
to its own `{app_id, private_key}`), with the
`REPORTER_APP_ID`/`REPORTER_APP_PRIVATE_KEY` pair as the fallback for any
owner not in the mapping — the same resolution order as `DISPATCH_APPS`.

### Token flow

The token flow matches the dispatch flow, with the direction reversed and a
narrower grant: `odp-releaser report-deployment` resolves the *source*
owner's reporter credentials, finds the app's installation on the single
source repo, and mints a one-hour token scoped to that repo with
`permissions: {deployments: write}` — nothing else. It then finds or creates
the deployment at the payload's `git_sha` and sets its status (`success` for
a direct commit, `queued` for a bump pull request that still needs review;
[`report-merged.yml`](workflows.md#report-merged) flips `queued` to
`success` when the bump PR merges).

`odp-releaser comment` runs the same flow for its own, separate token, scoped
to the same single repo with `permissions: {pull_requests: write}` and nothing
else. The two are deliberately never combined into one token request: GitHub
rejects a request for any permission the app hasn't been granted, so a single
`{deployments, pull_requests}` mint would fail outright — and take deployment
reporting down with it — for every source org that hasn't accepted the comment
permission yet.

### Pull request comments

Deployments are a precise record but a quiet one: they carry no image name, no
tag, and no indication of whether the change is live or still waiting on
review. The reporter app can also post a **comment** on the source pull
request saying all of that in words — `staged` while a bump pull request awaits
review in the deploy repo, rewritten as `deployed` once it merges. See
[Image manifest config](config/image_manifest.md) for the templates and
[`comment`](cli.md) for the command.

This is opt-in at the app level, because it is a real widening of what the
reporter app can do:

- **Add `Pull requests: Read and write`** to the app's repository
  permissions. Confirmed against GitHub's permission reference, that grant
  alone satisfies the comment endpoints — an `Issues` grant is not needed.
- **Every existing installation must accept the new permission.** GitHub sends
  each installation owner a permission request when an app adds one, and
  tokens keep the *old* permission set until it is accepted. Until a given
  source org accepts, that org's comments fail (loudly, in the step summary)
  while its deployment reports keep working — the two are minted as separate
  tokens precisely so one can't break the other.
- **It is a wider grant than `Deployments`.** `Pull requests: write` also
  allows editing pull request titles, bodies, labels, and reviewers on every
  repo the app is installed on. It cannot merge or push code (that needs
  `Contents`), but a source org weighing "installing the app *is* the consent"
  should know it is consenting to more than a deployment record. Leaving the
  permission off is a perfectly good answer: everything else keeps working.

Two further things worth knowing before turning it on:

- **Only `push` events can be commented on.** The comment lands on the pull
  request associated with the commit that built the image, which
  `odp-releaser notify` only resolves for `push` events. Release and
  `workflow_dispatch` dispatches have no pull request, and the comment step is
  skipped for them; `odp-releaser validate image-manifest` warns when a config
  asks for a comment it can never post.
- **Comments fire `issue_comment` in the source repo**, the same class of
  caveat as the `deployment` event above. Harmless unless a source workflow
  triggers `on: issue_comment`.

Each comment is keyed to one `(deploy repo, image, environment)` triple by an
invisible marker in its body, so reruns edit that same comment instead of
piling up, and a second deploy repo (or a second image) commenting on the same
pull request never overwrites the first.
