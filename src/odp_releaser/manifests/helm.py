from io import StringIO
from pathlib import Path

from yamlpath.enums import YAMLValueFormats

from odp_releaser.logger import logger
from odp_releaser.manifests.helpers import (
    apply_set_templates,
    open_for_editing,
    set_value,
    yaml,
)
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import HelmManifest


def update_helm_values_with_payload(
    values_path: Path,
    values_text: str,
    manifest: HelmManifest,
    payload: ClientPayload,
    commit_message: list[str],
) -> str:
    """Update a Helm values file for the given payload.

    Applies any ``manifest.set`` template paths (see
    :func:`odp_releaser.manifests.helpers.apply_set_templates`).

    When ``manifest.dagster_user_code`` is true, every entry in the top-level
    ``deployments`` list whose ``image.repository`` matches
    ``payload.image_name`` has its ``image.tag`` set to ``payload.new_tag()``.
    If no deployment matches, a warning is logged (the chart may be listed for
    future use) and the file is otherwise left unchanged.

    All other content and formatting is preserved via the same ruamel
    round-trip used for kustomize manifests.
    """
    processor = open_for_editing(values_text)
    logger.debug(f"Original values for {values_path}: {processor.data}")

    helm_message: list[str] = []

    apply_set_templates(processor, manifest.set, payload, helm_message)

    if manifest.dagster_user_code:
        tag_path = f'/deployments[image.repository="{payload.image_name}"]/image/tag'
        matches = list(processor.get_nodes(tag_path, mustexist=False))
        if matches:
            message = set_value(
                processor,
                tag_path,
                payload.new_tag(),
                mustexist=True,
                value_format=YAMLValueFormats.DQUOTE,
            )
            helm_message.append(f"  - {message}")
        else:
            logger.warning(
                f"No dagster deployment in {values_path} has an "
                f"image.repository of '{payload.image_name}'; leaving "
                "deployments unchanged"
            )

    stream = StringIO()

    yaml.dump(processor.data, stream)
    stream.seek(0)

    if len(helm_message) > 0:
        commit_message.append(f"- Updated helm values for {values_path}:")
        commit_message.extend(helm_message)

    return stream.read()
