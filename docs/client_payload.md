---
icon: lucide/braces
---

# Client Payload

The client payload is what is sent along with the `repository_dispatch` event by the code repo for the deployment repo to make decisions on how to handle.

## Example Payloads

### Push

```json
--8<-- "tests/event_data/client_payload/push.json"
```

### Release

```json
--8<-- "tests/event_data/client_payload/release.json"
```

### Workflow Dispatch

```json
--8<-- "tests/event_data/client_payload/workflow_dispatch.json"
```

## Client Payload Model

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.client_payload.ClientPayload
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.client_payload.ClientPayloadSource
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.client_payload.Release
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.client_payload.PullRequest
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```