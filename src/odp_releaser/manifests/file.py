import json
from io import StringIO
from pathlib import Path
from typing import Any

from odp_releaser.logger import logger
from odp_releaser.manifests.helpers import apply_set_templates, open_for_editing, yaml
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import FileManifest


def _to_plain(data: Any) -> Any:
    """Recursively convert ruamel ``CommentedMap``/``CommentedSeq`` to plain types.

    ``json.dumps`` cannot serialize ruamel's scalar/container subclasses in a
    stable way, so collapse them to built-in ``dict``/``list``/scalars first.
    """
    if isinstance(data, dict):
        return {key: _to_plain(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [_to_plain(item) for item in data]
    return data


def update_file_with_payload(
    file_path: Path,
    file_text: str,
    manifest: FileManifest,
    payload: ClientPayload,
    commit_message: list[str],
) -> str:
    """Update a generic YAML or JSON manifest via ``manifest.set`` paths.

    JSON is parsed as a YAML subset by ruamel. Files with a ``.json`` suffix
    are re-serialized with :func:`json.dumps` (2-space indent, trailing
    newline); everything else is dumped with the shared ruamel editor used by
    the other engines.
    """
    processor = open_for_editing(file_text)
    logger.debug(f"Original manifest for {file_path}: {processor.data}")

    commit_message.append(f"- Updated file manifest at {file_path}")

    apply_set_templates(processor, manifest.set, payload, commit_message)

    if file_path.suffix == ".json":
        return json.dumps(_to_plain(processor.data), indent=2) + "\n"

    stream = StringIO()
    yaml.dump(processor.data, stream)
    stream.seek(0)

    return stream.read()
