---
icon: lucide/pencil
---

# Image manifest

The image manifest is usually stored at `.github/image_manifest.yaml` in the deployment repos.

It can be tested with `odp-releaser test bump-images`.

## Example image manifest

Example `image_manifest.yaml` with documentation can be generated via the CLI.

```bash
$ odp-releaser generate-config image-manifest
```

```python exec="on" result="yaml"
from odp_releaser.schemas.manifest_config import ManifestConfig

print(ManifestConfig.generate_yaml())
```

## API

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.ManifestConfig
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.ImageConfig
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

### Manifests

For each of the manifest types, the `set` key takes a dictionary of [yamlpath](https://github.com/wwkimball/yamlpath#introduction) selectors and templated values to update.

#### Templated `set` values:

The values are templated with parts of the [client payload](../client_payload.md).

##### Example values

```python exec="on"
from odp_releaser.bump_image_tester import load_client_payload

payload = load_client_payload("push")
for key, value in payload.value_format_kwargs().items():
    if isinstance(value, str):
        print(f"- `{key}` - `{value}`")
    else:
        print(f"- `{key}` - `{value}`")
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.KustomizeManifest
    options:
      heading_level: 4
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.HelmManifest
    options:
      heading_level: 4
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.FileManifest
    options:
      heading_level: 4
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```
