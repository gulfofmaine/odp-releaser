from pathlib import Path
from typing import Annotated

import typer

from odp_releaser.bump_images import bump_images
from odp_releaser.logger import logger
from odp_releaser.schemas.client_payload import ClientPayload

CLIENT_PAYLOAD_DIR = Path(__file__).parent / "client_payload"


def bump_images_tester(
    ctx: typer.Context,
    config_path: Annotated[Path, typer.Argument(envvar="MANIFEST_CONFIG_PATH")],
    image_name: Annotated[str, typer.Argument()],
    event_type: Annotated[str, typer.Argument()],
) -> None:
    """Test bumping images with the given configuration, image name, and event type."""
    logger.debug(f"Context: {ctx.obj}")
    logger.debug(f"Config path: {config_path}")
    logger.debug(f"Image name: {image_name}")
    logger.debug(f"Event type: {event_type}")

    payload = load_client_payload(event_type)

    set_payload_image(image_name, payload)
    logger.warning(f"Calling bump_images with a {event_type} event for {image_name}")

    bump_images(
        config_path=config_path,
        client_payload=payload.model_dump_json(),
        dry_run=True,
    )


def set_payload_image(image_name: str, payload: ClientPayload) -> None:
    payload.image_name = image_name

    _, image_ref_digest = payload.image_ref.split("@")

    payload.image_ref = f"{image_name}@{image_ref_digest}"

    logger.debug("Replaced client payload:")
    logger.debug(payload)


def load_client_payload(event_type: str) -> ClientPayload:
    payload_path = CLIENT_PAYLOAD_DIR / f"{event_type}.json"

    logger.debug(f"Payload path: {payload_path}")
    payload = ClientPayload.model_validate_json(payload_path.read_text())
    logger.debug("Parsed client payload")
    logger.debug(payload)
    return payload
