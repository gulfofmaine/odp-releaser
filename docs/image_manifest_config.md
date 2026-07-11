---
icon: lucide/pencil
---

# Image manifest config

The image manifest is usually stored at `.github/image_manifest.yaml`.

## Example image manifest

```python exec="on" result="yaml"
from odp_releaser.schemas.manifest_config import ManifestConfig

print(ManifestConfig.generate_yaml())
```

## API

```python exec="on"
from odp_releaser.schemas.manifest_config import ManifestConfig, ImageConfig

print("### ManifestConfig")
print(ManifestConfig.generate_markdown().replace("# ", "> "))

print("### ImageConfig")
print(ImageConfig.generate_markdown().replace("# ", "> "))
```

### Manifests

For each of the manifest types, the `set` key takes a dictionary of [yamlpath](https://github.com/wwkimball/yamlpath#introduction) selectors and templated values to update.

#### Templated `set` values:

The values are templated with parts of the [client payload](./client_payload.md).

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

```python exec="on"
from odp_releaser.schemas.manifest_config import (
    KustomizeManifest,
    HelmManifest,
    FileManifest,
)

print("#### KustomizeManifest")
print(KustomizeManifest.generate_markdown().replace("# ", "> "))

print("#### HelmManifest")
print(HelmManifest.generate_markdown().replace("# ", "> "))

print("#### FileManifest")
print(FileManifest.generate_markdown().replace("# ", "> "))
```
