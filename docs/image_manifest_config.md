---
icon: lucide/pencil
---

# Image manifest configuration

## Example image manifest

```python exec="on" result="yaml"
from odp_releaser.schemas.manifest_config import ManifestConfig

print(ManifestConfig.generate_yaml())
```

## API

```python exec="on"
from odp_releaser.schemas.manifest_config import ManifestConfig

print(ManifestConfig.generate_markdown())
```

```md exec="true" updatetoc="false"
::: odp_releaser.schemas.manifest_config.ManifestConfig
    options:
      heading_level: 3
      extensions:
      - griffe_pydantic
      skip_local_inventory: true
```
