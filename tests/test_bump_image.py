from pathlib import Path

import pytest

from odp_releaser.bump_image_tester import load_client_payload, set_payload_image
from odp_releaser.bump_images import bump_images


def test_missmatched_sha_format_error():
    client_payload = load_client_payload("push")
    set_payload_image("gmri/neracoos-mariners-dashboard", client_payload)

    with pytest.raises(KeyError):
        bump_images(
            config_path=Path(__file__).parent
            / "manifests"
            / "key_error"
            / "image_manifest.yaml",
            client_payload=client_payload.model_dump_json(),
            dry_run=True,
        )
