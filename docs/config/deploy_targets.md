---
icon: lucide/crosshair
---

# Deploy targets

Configured in the source repos and used by the `odp-releaser notify` command.

Usually stored at ``.github/deploy_targets.yaml`.

They set `owner` and `repo` pairs to send notification events to, and optionally an `event_type` if the `repository_dispatch` is expecting another kind of event.

It can be tested with `odp-releaser test notify`.

## Example deploy targets

```bash
$ odp-releaser generate-config deploy-targets
```

```bash exec="on" result="yaml"
odp-releaser generate-config deploy-targets
```

## API

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.dispatch.DeployTarget
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

## Validating without dispatching

`odp-releaser test notify` builds a canned `client_payload` (the same ones
`test bump-images` uses) and reports, per configured target, whether dispatch
app credentials are available -- without minting any tokens or making any
network calls. It exits non-zero if the deploy targets file is missing or
fails to parse; an existing file that is empty or contains an empty array is
a valid no-op.

```bash
$ odp-releaser test notify --image-name gmri/neracoos-mariners-dashboard --event-type push
```
