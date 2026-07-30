---
icon: lucide/key-round
---

# GitHub Apps

`odp-releaser` moves container images across an organizational trust boundary: a
public **source** repo (which built and pushed an image) tells a private
**deploy** repo (which owns the Kubernetes/Helm manifests) that a new image is
ready. That message travels as a
[`repository_dispatch`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#repository_dispatch)
event, and sending one requires a token with `contents: write` on the **target**
repository — the default `GITHUB_TOKEN` a workflow run gets is scoped only to
the repo the workflow is running in, so it can never reach across repos, let
alone across orgs.

This page is the reference for *why* the app roles are shaped the way they are,
and exactly what each token can do. For the setup checklists, see
[deploy org admins](../deploy/github_app.md) and
[source repo maintainers](../source/github_app.md).

## The dispatch role

GitHub Apps close that gap. Each deploy org owns and runs its own dispatch app,
installs it only on its own deploy repos, and hands out a private key to each
source org it trusts.

- A deploy org can revoke a single source org's access (delete that org's key)
  without touching any other source org's ability to dispatch.
- No org ever holds a credential that reaches into another org's repositories —
  a compromised source-org secret only exposes the deploy repos that org was
  explicitly trusted with.
- Installing the app only on deploy repos (never source repos) keeps the blast
  radius of a leaked app private key limited to `contents`/PR-write on a known,
  small repo list.

### Token flow

For every deploy target, `odp-releaser notify` (via
`odp_releaser.github.send_dispatch`) runs the same sequence:

1. **Resolve credentials** for `target.owner` (`DISPATCH_APPS` mapping, then the
   default pair) — raises `MissingCredentialsError` if neither covers it.
2. **Authenticate as the App** with a JWT signed by the resolved private key.
3. **`GET /repos/{owner}/{repo}/installation`** to find the app's installation
   on the target repo — a 404 here raises `AppNotInstalledError`.
4. **Mint an installation access token** scoped to `repositories: [repo]` with
   `permissions: {contents: write}` — nothing broader than the single target
   repo, no matter what the app's own maximum permissions are.
5. **`POST /repos/{owner}/{repo}/dispatches`** with that token, sending
   `event_type` and the [`client_payload`](client_payload.md).

These tokens are short-lived (1 hour), created fresh for each target on each
run, and are never logged or written to disk — only exception messages,
`owner`/`repo`/`event_type`, and (at debug level) the client payload itself are
logged.

## The reporter role

The dispatch app role only ever pushes information one direction: source →
deploy. The **reporter app** works the other
direction: after `bump-images` lands a manifest change, the deploy repo can
report a
[GitHub deployment](https://docs.github.com/en/rest/deployments/deployments)
back onto the source repository at the commit that built the image. The source
repo's pull request timeline then shows "deployed to *environment*" entries, and
its Environments sidebar shows the latest deployed commit per deploy repo — with
no comment formatting or notification plumbing.

Unlike the dispatch role — where per-source-org keys guard a powerful
`contents: write` grant — the reporter only ever needs `deployments: write`, a
low-stakes permission that can create deployment records but never touch code.
That makes a much simpler ownership model the recommended default: **one app,
owned by the deploy org, installed by each source org that wants reports**. No
private key is ever shared; installing the app *is* the consent, and
uninstalling it *is* the revocation.

### Alternative: source-org-owned reporter apps

Orgs that prefer the dispatch model's tighter blast-radius can mirror it
instead: the *source* org owns the reporter app, installs it on its own repos,
and hands a private key to each deploy org it wants reports from (one key per
deploy org, revocable individually). The tradeoff is real key sharing and
rotation work — justified when a leaked deploy-org secret reaching *all*
installed source repos' deployments is a concern, less so given the permission
can't modify code.

The CLI supports both models with the same secrets: per-source-owner credentials
go in a `REPORTER_APPS` JSON object (mapping each source owner to its own
`{app_id, private_key}`), with the `REPORTER_APP_ID`/`REPORTER_APP_PRIVATE_KEY`
pair as the fallback for any owner not in the mapping — the same resolution
order as `DISPATCH_APPS`.

### Token flow

The token flow matches the dispatch flow, with the direction reversed and a
narrower grant: `odp-releaser report-deployment` resolves the *source* owner's
reporter credentials, finds the app's installation on the single source repo,
and mints a one-hour token scoped to that repo with
`permissions: {deployments: write}` — nothing else. It then finds or creates the
deployment at the payload's `git_sha` and sets its status (`success` for a
direct commit, `queued` for a bump pull request that still needs review;
[`report-merged.yml`](../deploy/report_merged.md) flips `queued` to `success`
when the bump PR merges).

`odp-releaser comment` runs the same flow for its own, separate token, scoped to
the same single repo with `permissions: {pull_requests: write}` and nothing
else. The two are deliberately never combined into one token request: GitHub
rejects a request for any permission the app hasn't been granted, so a single
`{deployments, pull_requests}` mint would fail outright — and take deployment
reporting down with it — for every source org that hasn't accepted the comment
permission yet.

## Pull request comments

Deployments are a precise record but a quiet one: they carry no image name, no
tag, and no indication of whether the change is live or still waiting on review.
The reporter app can also post a **comment** on the source pull request saying
that — `staged` while a bump pull request awaits review,
rewritten as `deployed` once it merges. See
[Pull request comments](../deploy/image_manifest.md#pull-request-comments) for
the templates and [the CLI reference](cli.md) for the command.

This is opt-in at the app level:

- **Add `Pull requests: Read and write`** to the app's repository permissions.
  Confirmed against GitHub's permission reference, that grant alone satisfies
  the comment endpoints — an `Issues` grant is not needed.
- **It is a wider grant than `Deployments`.** `Pull requests: write` also allows
  editing pull request titles, bodies, labels, and reviewers on every repo the
  app is installed on. It cannot merge or push code (that needs `Contents`), but
  a source org weighing "installing the app *is* the consent" should know it is
  consenting to more than a deployment record. Leaving the permission off is a
  perfectly good answer: everything else keeps working.

Two further things worth knowing before turning it on:

- **Only `push` events can be commented on.** The comment lands on the pull
  request associated with the commit that built the image, which
  `odp-releaser notify` only resolves for `push` events. Release and
  `workflow_dispatch` dispatches have no pull request, and the comment step is
  skipped for them; `odp-releaser validate image-manifest` warns when a config
  asks for a comment it can never post.
- **Comments fire `issue_comment` in the source repo**, the same class of caveat
  as the `deployment` event a report fires. Harmless unless a source workflow
  triggers `on: issue_comment`.

Each comment is keyed to one `(deploy repo, image, environment)` triple by an
invisible marker in its body, so reruns edit that same comment instead of piling
up, and a second deploy repo (or a second image) commenting on the same pull
request never overwrites the first.
