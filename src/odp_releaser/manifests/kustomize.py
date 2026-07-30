from io import StringIO
from pathlib import Path
from typing import Literal

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


def image_entry_path(image_name: str) -> str:
    """The yamlpath selector for a kustomize ``images:`` entry, by name.

    Exported so the config validator checks the exact selector
    ``update_kustomize_with_payload`` resolves against; a hand-respelled copy
    in the validator could drift in quoting or structure and then pass or
    fail nodes a real bump would not.
    """
    return f"""/images[name="{image_name}"]"""


def image_pin_path(image_name: str, pin: Literal["tag", "digest"]) -> str:
    """The yamlpath selector for the field ``pin`` writes on an images entry.

    Takes the same ``"tag"``/``"digest"`` literal
    :attr:`KustomizeManifest.pin` uses, so a new pin mode can't be wired into
    this engine (``newTag``/``digest`` are the only two fields it knows how
    to write) without this builder -- and the validator that shares it --
    also being taught about it.
    """
    field = "digest" if pin == "digest" else "newTag"
    return f"{image_entry_path(image_name)}/{field}"


def update_kustomize_with_payload(
    kustomize_path: Path,
    kustomize_text: str,
    manifest: KustomizeManifest,
    payload: ClientPayload,
    deployed_name: str,
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

    ``deployed_name`` (see ``bump_images.effective_deployed_name``) is
    deliberately *not* used to select the ``images:`` entry: kustomize's own
    ``newName`` field is what actually carries a mirrored image name, so the
    entry stays keyed on ``payload.image_name`` (the upstream name) whether
    or not this config declares a ``deployed_as``. Passing ``deployed_name``
    into the selector here would instead break the match for a repo that
    mirrors -- ``newName`` and the ``name=`` key describe two different
    things: what kustomize renders the image as, versus which entry to edit.
    ``deployed_name`` is only threaded through for ``set`` templating (see
    ``apply_set_templates``), same as the other engines.
    """
    processor = open_for_editing(kustomize_text)
    logger.debug(f"Original manifest for {kustomize_path}: {processor.data}")

    commit_message.append(f"- Updated kustomize manifest at {kustomize_path}")

    if manifest.pin == "digest":
        set_path = image_pin_path(payload.image_name, manifest.pin)
        message = set_value(
            processor,
            set_path,
            payload.digest,
            mustexist=False,
            value_format=YAMLValueFormats.DQUOTE,
        )
    else:
        set_path = image_pin_path(payload.image_name, manifest.pin)
        message = set_value(
            processor,
            set_path,
            payload.new_tag(),
            mustexist=True,
            value_format=YAMLValueFormats.DQUOTE,
        )
    commit_message.append(f"  - {message}")

    apply_set_templates(processor, manifest.set, payload, deployed_name, commit_message)

    stream = StringIO()

    yaml.dump(processor.data, stream)
    stream.seek(0)

    return stream.read()
