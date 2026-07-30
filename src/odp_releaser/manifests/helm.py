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
    deployed_name: str,
    commit_message: list[str],
) -> str:
    """Update a Helm values file for the given payload.

    Applies any ``manifest.set`` template paths (see
    :func:`odp_releaser.manifests.helpers.apply_set_templates`).

    When ``manifest.dagster_user_code`` is true, the entry in the top-level
    ``deployments`` list whose ``image.repository`` matches
    ``deployed_name`` (see :meth:`ImageConfig.deployed_name` --
    ``ImageConfig.deployed_as`` when set, otherwise ``payload.image_name``)
    has its ``image.tag`` set to ``payload.new_tag()``. The write uses
    ``mustexist=True``, so if no deployment matches, this raises (mirroring
    the kustomize tag-pin engine, see ``update_kustomize_with_payload``)
    instead of silently no-oping.

    This is the one place ``deployed_name`` (rather than
    ``payload.image_name``) drives *which entry gets matched*, and it is
    deliberately asymmetric with the kustomize engine: kustomize's own
    ``newName`` field carries a mirrored image name while the ``images:``
    entry itself stays keyed on the upstream name, so kustomize never needed
    ``deployed_name`` for its selector. The Helm dagster shorthand has no
    such indirection -- ``image.repository`` in the values file *is* the
    name actually deployed -- so when a config's manifests run on a mirrored
    image (e.g. an ECR pull-through cache path), ``image.repository`` holds
    the mirrored name and only matching on ``deployed_name`` makes this
    entry bumpable at all. Neither engine is wrong; they carry the mirror in
    different places.

    All other content and formatting is preserved via the same ruamel
    round-trip used for kustomize manifests.
    """
    processor = open_for_editing(values_text)
    logger.debug(f"Original values for {values_path}: {processor.data}")

    helm_message: list[str] = []

    apply_set_templates(processor, manifest.set, payload, deployed_name, helm_message)

    if manifest.dagster_user_code:
        tag_path = dagster_tag_path(deployed_name)
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
