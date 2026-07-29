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


def dagster_deployment_path(image_name: str) -> str:
    """The yamlpath selector for a dagster user-deployments entry, by repository.

    Exported so the config validator's "no matching deployment" check looks
    at the exact same entry this engine matches against; a hand-respelled
    copy could drift from what actually gets written.
    """
    return f'/deployments[image.repository="{image_name}"]'


def dagster_tag_path(image_name: str) -> str:
    """The yamlpath selector for the ``image.tag`` leaf under a deployment entry.

    Defined in terms of :func:`dagster_deployment_path` so the entry
    selector and the leaf this engine actually writes can't diverge if one
    is edited without the other.
    """
    return f"{dagster_deployment_path(image_name)}/image/tag"


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

    When ``manifest.dagster_user_code`` is true, the entry in the top-level
    ``deployments`` list whose ``image.repository`` matches
    ``payload.image_name`` has its ``image.tag`` set to ``payload.new_tag()``.
    The write uses ``mustexist=True``, so if no deployment matches, this
    raises (mirroring the kustomize tag-pin engine, see
    ``update_kustomize_with_payload``) instead of silently no-oping.

    All other content and formatting is preserved via the same ruamel
    round-trip used for kustomize manifests.
    """
    processor = open_for_editing(values_text)
    logger.debug(f"Original values for {values_path}: {processor.data}")

    helm_message: list[str] = []

    apply_set_templates(processor, manifest.set, payload, helm_message)

    if manifest.dagster_user_code:
        tag_path = dagster_tag_path(payload.image_name)
        message = set_value(
            processor,
            tag_path,
            payload.new_tag(),
            mustexist=True,
            value_format=YAMLValueFormats.DQUOTE,
        )
        helm_message.append(f"  - {message}")

    stream = StringIO()

    yaml.dump(processor.data, stream)
    stream.seek(0)

    if len(helm_message) > 0:
        commit_message.append(f"- Updated helm values for {values_path}:")
        commit_message.extend(helm_message)

    return stream.read()
