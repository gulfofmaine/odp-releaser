---
icon: lucide/crosshair
---

# Deploy targets

Configured in the source repos and used by the
[`notify` workflow](notify.md), which dispatches one `repository_dispatch`
event per entry.

Usually stored at `.github/deploy_targets.yaml`.

## Example deploy targets

A YAML array of deploy targets (a JSON array also parses — YAML is a superset of
JSON). Generate a starter file with:

```bash
$ odp-releaser generate-config deploy-targets
```

```bash exec="on" result="yaml"
odp-releaser generate-config deploy-targets
```

Each entry:

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `owner` | yes | — | Owner of the deploy repository. |
| `repo` | yes | — | Name of the deploy repository. |
| `event_type` | no | `image-published` | `repository_dispatch` event type to send. Set it when the deploy repo's workflow is listening for something other than the default. |

A missing file is an error: `notify` exits non-zero and suggests generating one
with `odp-releaser generate-config deploy-targets`. A file that is empty or
contains an empty array is also an error — a targets file with nothing to
dispatch to is treated as a misconfiguration rather than a silent no-op, so
`notify` exits non-zero.

## API

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.dispatch.DeployTarget
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

## Validating

`odp-releaser validate deploy-targets` runs every check below statically and
offline: no GitHub API calls, no writes, just the config file(s) on disk. It's
meant to run in CI, as a [pre-commit hook](../getting-started.md#pre-commit-hooks),
or by hand before a config change is merged.

```bash
odp-releaser validate deploy-targets
odp-releaser validate deploy-targets .github/deploy_targets.yaml
```

A clean file prints a one-line `✓ <path>` to stdout. Problems are printed to
stderr as `path:line: severity: message`, one per line, colored by severity. The
command exits `1` if any file has errors, or with `--strict`, if any file has
warnings, so a repo that wants warnings to block CI can opt in:

```bash
odp-releaser validate deploy-targets --strict
```

To check that credentials resolve for every target without dispatching anything,
use [`odp-releaser test notify`](../development/testing.md#testing-a-deploy-targets-config).

### What is checked

A config that is *shaped* correctly can still *mean*
something the runtime code mishandles. The tables below list each check's
consequence at dispatch time.

**Errors** (always fail the run):

| Check | Consequence if not caught |
| --- | --- |
| File missing, empty, unparsable, or a schema mismatch | `notify` cannot load any targets at all |
| Unknown key on a target | Silently dropped by pydantic (e.g. a typo'd `repoo:` dispatches with no repo) |
| `owner` or `repo` empty/whitespace-only | `repository_dispatch` has nothing to target |
| `repo` contains `/` | The owner belongs in `owner`; as written, dispatch is sent to a repository that doesn't exist |
| `owner`/`repo` contains characters GitHub doesn't allow, or starts/ends with `-` | Can never match a real organization, user, or repository name |
| `event_type` empty/whitespace-only | `repository_dispatch` requires a non-empty `event_type` |

**Warnings** (reported; fail the run only with `--strict`):

| Check | Consequence if not caught |
| --- | --- |
| Two targets share the same `(owner, repo, event_type)` triple | The same dispatch is sent twice |
