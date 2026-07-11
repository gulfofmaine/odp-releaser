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
from odp_releaser.schemas.manifest_config import KustomizeManifest


def update_kustomize_with_payload(
    kustomize_path: Path,
    kustomize_text: str,
    manifest: KustomizeManifest,
    payload: ClientPayload,
    commit_message: list[str],
) -> str:
    """Update an image entry (and any extra ``set`` paths) in a kustomization file.

    ``manifest.pin`` controls which field on the matching
    ``/images[name=...]`` entry is written:

    - ``"tag"`` (the default): ``newTag`` is set to the release tag.
    - ``"digest"``: the immutable ``digest`` field is set to the image
      digest instead.

    Only the pinned field is written. Kustomize itself prefers ``digest``
    over ``newTag`` when both are present, but this function doesn't also
    clear or set the other field -- if a manifest already carries the field
    for the *other* pin mode (e.g. a stale ``newTag`` left over from before a
    switch to digest pinning), that's left untouched; reconciling it is on
    the operator.
    """
    processor = open_for_editing(kustomize_text)
    logger.debug(f"Original manifest for {kustomize_path}: {processor.data}")

    commit_message.append(f"- Updated kustomize manifest at {kustomize_path}")

    if manifest.pin == "digest":
        set_path = f"""/images[name="{payload.image_name}"]/digest"""
        message = set_value(
            processor,
            set_path,
            payload.digest,
            mustexist=False,
            value_format=YAMLValueFormats.DQUOTE,
        )
    else:
        set_path = f"""/images[name="{payload.image_name}"]/newTag"""
        message = set_value(
            processor,
            set_path,
            payload.new_tag(),
            mustexist=True,
            value_format=YAMLValueFormats.DQUOTE,
        )
    commit_message.append(f"  - {message}")

    apply_set_templates(processor, manifest.set, payload, commit_message)

    stream = StringIO()

    yaml.dump(processor.data, stream)
    stream.seek(0)

    return stream.read()
