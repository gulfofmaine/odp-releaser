---
icon: lucide/copy
---

# Syncing images

Every manifest engine keys its update off the payload's own `image_name` by
default. This can be changed with two `ImageConfig` settings change that when a deploy repo doesn't
deploy the payload's image name verbatim — most commonly because it pulls
through a cloud-native registry mirror instead of the upstream registry:

- **`deployed_as`** — the image name the manifests under *this config* actually
  deploy from, when it differs from the payload's `image_name`. E.g. an ECR
  pull-through cache path such as
  `123456789.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/sea-eagle-brown-3crs`
  mirroring upstream `gmri/sea-eagle-brown-3crs`. Unset falls back to the
  payload's `image_name`.
- **`sync`** (default `false`) — whether odp-releaser should actually copy the
  payload's image to `deployed_as` before the bump is committed.

**`deployed_as` is per-config only.** Unlike other settings on
`ImageConfig`, there is no `defaults.deployed_as`.

## The common case: declare-only, no `sync`

**Leave `sync` unset (or don't set `deployed_as` at all) for registry-native
replication** — an ECR pull-through cache being the common case here. A
pull-through cache populates itself the first time something pulls the mirrored
path, and there is no way to push into one directly, so there is nothing for
`sync` to do:

```yaml
images:
  gmri/sea-eagle-brown-3crs:
    - events: [push]
      deployed_as: 123456789.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/sea-eagle-brown-3crs
      kustomize_manifests:
        - ./kustomization.yaml
      helm_charts:
        - path: ./values.yaml
          dagster_user_code: true
```

## Active mirroring: `sync: true`

Set `sync: true` when the destination registry needs the image actively
pushed to it (ex: GHCR, Docker Hub, or a plain, non-pull-through ECR repository):

```yaml
images:
  ghcr.io/gulfofmaine/neracoos-mariners-dashboard-dev:
    - events: [push]
      deployed_as: gmri/neracoos-mariners-dashboard
      sync: true
      kustomize_manifests:
        - path: apps/mariners-dev/kustomization.yaml
          pin: digest
```

The [`bump_images` action](../api/actions.md#bump_images) copies the image to
that registry, between writing the manifests and committing them, using
`skopeo copy --all --preserve-digests`, then checks the resulting digest and
skips a destination that already carries it.

!!! warning "A failed copy fails the bump"
    A merged manifest pointing at an image nobody pushed is an outage.

Set the [`sync` input](bump_images.md) to `false` on the calling job
to disable the sync workflow-wide, as a kill switch independent of what any
individual image manifest declares.

!!! warning "A rejected bump PR still occupies the destination tag"

    In `pull_request` mode the copy happens before the PR opens, so an
    abandoned or rejected bump leaves `deployed_as:<tag>` populated. On a
    tag-immutable destination (ECR, or GHCR with immutability enabled), a
    later dispatch of a *different* digest to that same tag can then never be
    synced: the push is rejected, and the skip-if-already-there check does not
    apply because the digests differ. Re-dispatching the *same* digest is
    fine, which is the case the skip check covers. Deleting the orphaned tag
    is currently manual.

## Credentials

Provide destination credentials one of two ways:

- `sync_aws_role_arn` (+ `sync_aws_region`) for an ECR destination reached via
  OIDC — see below.
- `sync_registry` / `sync_username` / `sync_password` secrets for a destination
  that uses plain username/password auth (GHCR, Docker Hub).

The copy also has to **pull**, and the source is the upstream registry the
payload names, which is often not the destination host. Set
`sync_source_registry` / `sync_source_username` / `sync_source_password` when
that source needs credentials the runner doesn't already have: a private
registry, or Docker Hub, where an anonymous pull draws on the shared runner IP's
rate limit. Because a failed copy fails the bump by design, an unauthenticated
source turns a rate limit into a blocked release.

If you compose the [`bump_images` action](../api/actions.md#bump_images)
yourself rather than calling the workflow, the login is your job: skopeo reads
credentials from `$HOME/.docker/config.json` via the containers credential
search order, so a preceding `docker/login-action` (or
`configure-aws-credentials` + `amazon-ecr-login`) step is enough, with no
separate `skopeo login`. A caller that configures a Docker *credential helper*
instead is the exception — the credentials are then not in that file, and skopeo
won't resolve them.

### Syncing to ECR via OIDC

Set `sync_aws_role_arn` (and `sync_aws_region`) to have
[`bump-images.yml`](bump_images.md) assume an IAM role via OIDC and log in to
ECR before the bump runs — no long-lived AWS credentials stored as secrets. This
is why every caller needs `id-token: write`, whether or not it actually syncs.

!!! warning "`id-token: write` is a privilege grant"

    Granting it means every step in that job can mint an OIDC token scoped to
    the calling repository, including this workflow's own steps and any action
    they call. Anything that trusts your repo's OIDC subject is therefore
    reachable from that job. `permissions` cannot be made conditional, so a
    caller that never syncs still has to grant it to call this workflow at all.
    If that trade isn't acceptable, compose the
    [`bump_images` action](../api/actions.md#bump_images) directly in your own
    workflow instead, where you decide the job's permissions.

The role's trust policy keys on the **calling** repo, not on this reusable
workflow:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:my-org/my-deploy-repo:*"
        }
      }
    }
  ]
}
```

The OIDC subject claim (`sub`) describes the workflow **run's repository** (the
deploy repo that called `bump-images.yml`) even though the actual
[`aws-actions/configure-aws-credentials` login](https://github.com/aws-actions/configure-aws-credentials) runs inside the reusable workflow.
So each deploy repo gets its own tightly scoped role keyed on
`repo:<that repo>:*`, and nothing needs to trust every caller of this shared
workflow via `job_workflow_ref`.

## Kustomize vs. Helm: where the mirror name lives

The two manifest engines carry a mirrored name in different places, so
`deployed_as` interacts with each differently — this is the one thing worth
understanding well before relying on either:

- **Kustomize** keeps matching `/images[name="<payload image_name>"]` — the
  `images:` entry itself always stays keyed on the *upstream* name. Kustomize's
  own `newName` field is the mirror, so `deployed_as` must equal that entry's
  `newName`:

  ```yaml
  images:
    - name: gmri/sea-eagle-brown-3crs # upstream name -- always the match key
      newName: 123456789.dkr.ecr.us-east-1.amazonaws.com/docker-hub/gmri/sea-eagle-brown-3crs # must equal deployed_as
      newTag: "ee1cadc"
  ```

  [`odp-releaser validate image-manifest`](image_manifest.md#validating) checks
  this agreement: a missing or disagreeing `newName` is an error, and a
  `newName` with no `deployed_as` declared is a warning (the mirror would then
  be invisible to `sync` and to the Helm shorthand below).

- **The Helm dagster user-deployments shorthand** (`dagster_user_code: true`)
  instead matches `/deployments[image.repository="<deployed_as>"]`, because that
  chart's values layout has no `newName` equivalent — `image.repository` *is*
  the deployed name. Before `deployed_as` existed, a values file whose
  `image.repository` already named a mirror could not be bumped at all: nothing
  selected it.

A config with both a kustomize manifest and a dagster-shorthand Helm manifest
for the same mirrored image needs no special coordination: `deployed_as` is the
one setting both engines key off, each in the place that engine actually carries
a mirror.

## The `{deployed_image}` placeholder

`set` values — on any of the three manifest types — can also reference
`{deployed_image}`, the same name just described (`deployed_as` when set,
otherwise the payload's own `image_name`). This lets a `file_manifests` entry
write the mirror registry directly instead of hand-typing it:

```yaml
file_manifests:
  - path: ../apps/config/deployment.json
    set:
      /spec/template/spec/containers[0]/image: "{deployed_image}@{digest}"
```

[`odp-releaser validate image-manifest`](image_manifest.md#validating) warns
when a `set` value hard-codes the *upstream* image name literally on a config
that also sets `deployed_as` — almost always meant to be `{deployed_image}`
instead.

!!! note "A shape limitation: no registry ports"

    `deployed_as` follows the same shape rule as an `images:` key: non-empty,
    trimmed, lowercase, and free of `@` and `:`. That last rule means a registry
    host with an explicit port (`registry.example.com:5000/foo`) currently can't
    be expressed as a `deployed_as` -- the same pre-existing limitation an
    `images:` key and a payload `image_name` already share.
