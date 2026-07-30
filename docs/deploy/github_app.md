---
icon: lucide/key-round
---

# GitHub Apps

A deploy org owns the apps in this relationship: the **dispatch app** that
source repos use to notify its deploy repos, and the **reporter app** that reaches back out to report deployments. The trust boundary that this ownership split gives is covered in
[GitHub Apps](../api/github_apps.md); this page is the setup checklist.

## The dispatch app

### 1. Create the app

In your deploy org's settings, go to **Settings → Developer settings → GitHub
Apps → New GitHub App** (org-level, not a personal app) and configure:

- **Permissions**: Repository permissions → `Contents: Read and write` and
  `Pull requests: Read and write`. Nothing else is required — the app never
  needs issues, actions, or admin permissions.
- **Webhooks**: turn the "Active" toggle off. This app is only ever used to mint
  tokens on demand; it doesn't need to receive events.
- **Where can this app be installed?**: "Only on this account" is sufficient
  unless you have a reason to allow installs elsewhere.
- **Name**: suggested pattern `<org>-odp-dispatch`, e.g.
  `gulfofmaine-odp-dispatch`. One app per deploy org keeps the "who can dispatch
  into my repos" question answerable at a glance.

After creating the app, note its **App ID** (shown on the app's settings page) —
you'll share it alongside each private key.

### 2. Install it on your deploy repos

From the app's settings page, **Install App**, and select only the repositories
that should receive dispatch events (or "All repositories" if that matches your
org's posture). The app must be installed on a repo before any source org can
dispatch to it — an install-less target fails with the
"[app not installed](../source/github_app.md#failure-modes-and-fixes)" error.

### 3. Generate one private key per trusted source org

Under the app's **Private keys** section, click **Generate a private key** once
per source org you trust, and keep track of which `.pem` belongs to which org.
Share the app ID and that org's private key with the source org's maintainers (a
password manager or secrets-sharing tool, not email/Slack in plaintext).

### 4. Rotation and revocation

- **Revoke a source org**: delete that org's private key from the app's settings
  page. Their next dispatch attempt fails with "no credentials for owner" once
  they've removed the stale secret, or with an authentication error immediately
  since GitHub invalidates JWTs signed with a deleted key.
- **Rotate a key**: generate a new one, share it with the source org, have them
  update their secret, then delete the old key.
- **Installation access tokens need no separate revocation.** They're minted per
  dispatch, live for one hour, and are scoped to a single repository, so there's nothing sitting around to leak.

### 5. Reusing the dispatch app to trigger your own CI

If any `ImageConfig` in your manifest config uses `update_mode: pull_request`
(see [Image manifest](image_manifest.md)), the bump workflow opens a pull
request instead of committing directly. A PR (or push) made with the default
`GITHUB_TOKEN` does **not** trigger further workflow runs, (this is a
GitHub Actions anti-recursion rule) so your repo's own CI would never run
against the bump PR unless you supply an app-minted token instead.

Since your deploy org already owns a dispatch app with `contents: write` +
`pull-requests: write`, you can reuse that same app's credentials for this: pass
its App ID and private key to [`bump-images.yml`](bump_images.md) as the
`ci_app_id` / `ci_app_private_key` secrets. When set, the workflow mints a token
with that app before checkout, and both the commit-and-push and the pull-request
paths use it instead of `GITHUB_TOKEN` — so the resulting commit/PR is authored
by your app and does trigger your CI.

If any image manifest uses `team_reviewers`, also grant the app the organization
`Members: Read-only` permission — requesting a team review needs it.

## The reporter app

The dispatch app only ever pushes information one direction: source → deploy.
The **reporter app** works other way, so a bump shows up
on the source repo's pull request timeline and Environments sidebar.

The recommended model is **one app, owned by the deploy org, installed by each
source org that wants reports** — no private key is ever shared. Create it in
your deploy org's settings, like the dispatch app but with:

- **Permissions**: Repository permissions → `Deployments: Read and write`.
    - Add `Pull requests: Read and write` only if you want bumps to
    [comment on the source pull request](bump_images.md#commenting-on-the-source-pull-request)
    as well.
    - Add Organization permissions → `Members: Read-only` only if your deploy
    repos use [`allowed_actors` team entries](bump_images.md#allowed-source-repos-and-actors)
    in their image manifests; the membership check runs with this app's
    credentials, since it's the one installed where the teams live.
- **Webhooks**: off, same as the dispatch app.
- **Where can this app be installed?**: **"Any account"** — source orgs must be
  able to install it on their own repos.
- **Name**: suggested pattern `<org>-odp-reporter`, e.g.
  `gulfofmaine-odp-reporter`.

Generate a single private key and store it — together with the App ID — as
GitHub Actions secrets in your deploy repos (or org): `REPORTER_APP_ID` /
`REPORTER_APP_PRIVATE_KEY`. The key never leaves your org. Then share the app's
public install link with each source org.

Pass the secrets to [`bump-images.yml`](bump_images.md) and
[`report-merged.yml`](report_merged.md) as the `reporter_app_id` /
`reporter_app_private_key` secrets. In `bump-images.yml` reporting is
best-effort: a failed report never fails the bump itself.

If a source org would rather own its reporter app and hand you a key instead,
that's supported with the same secrets — see
[Alternative: source-org-owned reporter apps](../api/github_apps.md#alternative-source-org-owned-reporter-apps).
