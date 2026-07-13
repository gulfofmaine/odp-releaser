from enum import StrEnum
from pathlib import Path
from typing import Annotated

import ruamel.yaml
import typer
from pydantic import ValidationError
from ruamel.yaml.error import YAMLError

from odp_releaser.bump_images import DEFAULT_CONFIG_PATH, bump_images
from odp_releaser.logger import logger
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.manifest_config import ManifestConfig

CLIENT_PAYLOAD_DIR = Path(__file__).parent / "client_payload"


class EventType(StrEnum):
    """Canned ``client_payload`` events available under ``client_payload/``."""

    push = "push"  # pylint: disable=invalid-name
    release = "release"  # pylint: disable=invalid-name
    workflow_dispatch = "workflow_dispatch"  # pylint: disable=invalid-name


def test_bump_images(
    ctx: typer.Context,
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config-path",
            envvar="MANIFEST_CONFIG_PATH",
            help=(
                "Path to the image_manifest.yaml config to test against. "
                "Prompted interactively when omitted."
            ),
        ),
    ] = None,
    image_name: Annotated[
        str | None,
        typer.Option(
            "--image-name",
            help=(
                "Name of the image to substitute into the canned client "
                "payload. Prompted interactively when omitted, listing the "
                "config's configured image names once the config path is "
                "known."
            ),
        ),
    ] = None,
    event_type: Annotated[
        EventType | None,
        typer.Option(
            "--event-type",
            help=(
                "Canned client_payload event to load: push, release, or "
                "workflow_dispatch. Prompted interactively when omitted."
            ),
        ),
    ] = None,
) -> None:
    """Test bumping images with the given configuration, image name, and event type.

    Runs `bump-images` in dry-run mode against a canned `client_payload` for
    the chosen event type, with the image name substituted in. Any of
    `--config-path`, `--image-name`, or `--event-type` left unset is prompted
    for interactively, so pass all three explicitly to run this non-interactively
    (e.g. in CI).
    """
    logger.debug(f"Context: {ctx.obj}")

    if config_path is None:
        config_path = Path(
            typer.prompt("Config path", default=str(DEFAULT_CONFIG_PATH))
        )
    logger.debug(f"Config path: {config_path}")

    if image_name is None:
        image_name = prompt_image_name(_image_names(config_path))
    logger.debug(f"Image name: {image_name}")

    if event_type is None:
        event_type = prompt_event_type()
    logger.debug(f"Event type: {event_type}")

    payload = load_client_payload(event_type)

    set_payload_image(image_name, payload)
    logger.warning(f"Calling bump_images with a {event_type} event for {image_name}")

    bump_images(
        config_path=config_path,
        client_payload=payload.model_dump_json(),
        dry_run=True,
    )


def prompt_event_type() -> EventType:
    """Interactively prompt for one of the canned client_payload event types."""
    options = ", ".join(member.value for member in EventType)
    while True:
        selection = typer.prompt(f"Event type ({options})")
        try:
            return EventType(selection)
        except ValueError:
            typer.echo(
                f"Invalid event type: {selection!r}. Choose from: {options}",
                err=True,
            )


def prompt_image_name(image_names: list[str] | None = None) -> str:
    """Interactively prompt for an image name, hinting at configured images."""
    prompt_text = "Image name to test"
    if image_names:
        prompt_text += f" (configured images: {', '.join(image_names)})"
    return str(typer.prompt(prompt_text))


def _image_names(config_path: Path) -> list[str] | None:
    """Best-effort list of configured image names, for a helpful prompt.

    Returns `None` when the config can't be read or doesn't parse as a
    `ManifestConfig`, so the image name prompt can degrade gracefully instead
    of erroring before the real validation in `bump_images` runs.
    """
    try:
        raw_config = config_path.read_text()
    except OSError:
        return None

    yaml = ruamel.yaml.YAML(typ="safe", pure=True)
    try:
        config = ManifestConfig.model_validate(yaml.load(raw_config))
    except (YAMLError, ValidationError):
        return None
    return sorted(config.images)


def set_payload_image(image_name: str, payload: ClientPayload) -> None:
    payload.image_name = image_name

    _, image_ref_digest = payload.image_ref.split("@")

    payload.image_ref = f"{image_name}@{image_ref_digest}"

    logger.debug("Replaced client payload:")
    logger.debug(payload)


def load_client_payload(event_type: EventType) -> ClientPayload:
    payload_path = CLIENT_PAYLOAD_DIR / f"{event_type.value}.json"

    logger.debug(f"Payload path: {payload_path}")
    payload = ClientPayload.model_validate_json(payload_path.read_text())
    logger.debug("Parsed client payload")
    logger.debug(payload)
    return payload
