from pathlib import Path
from io import StringIO

from odp_releaser.logger import logger
from odp_releaser.manifests.helpers import open_for_editing, yaml, set_value
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import KustomizeManifest


def update_kustomize_with_payload(kustomize_path: Path, kustomize_text: str, manifest: KustomizeManifest, payload: ClientPayload, commit_message: list[str]):
    processor = open_for_editing(kustomize_text)
    logger.debug(f"Origional manifest for {kustomize_path}: {processor.data}")

    commit_message.append(f"- Updated kustomize manifest at {kustomize_path}")

    set_path = f"""/images[name="{payload.image_name}"]/newTag"""
    formatted_value = f"'{payload.new_tag()}'"
    message = set_value(processor, set_path, formatted_value, mustexist=True)
    commit_message.append(f"  - {message}")

    for set_path, value in manifest.set.items():
        try:
            # processor.set_value(set_path, value.format(new_tag=payload.new_tag(), git_sha=payload.git_sha), mustexist=True)
            formatted_value = value.format(new_tag=payload.new_tag(), git_sha=payload.git_sha)
            message = set_value(processor, set_path, formatted_value, mustexist=True)
            commit_message.append(f"  - {message}")
        except KeyError as e:
            raise KeyError(f"Error setting value for path '{set_path}' with value '{value}'") from e

    # logger.warning(f"Updated: {processor.data}")

    stream = StringIO()

    yaml.dump(processor.data, stream)
    stream.seek(0)

    return stream.read()

    
