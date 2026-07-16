from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from odp_releaser.bump_image_tester import EventType, load_client_payload
from odp_releaser.report_metadata import (
    MARKER,
    ReportMetadata,
    embed_metadata,
    extract_metadata,
)


def _metadata(**kwargs: str | None) -> ReportMetadata:
    return ReportMetadata(
        client_payload=load_client_payload(EventType.push),
        **kwargs,
    )


def test_round_trip() -> None:
    metadata = _metadata(
        environment="production",
        environment_url="https://mariners.example.com",
    )

    body = f"Some PR body text.\n\n{embed_metadata(metadata)}"

    assert extract_metadata(body) == metadata


def test_round_trip_with_defaults() -> None:
    metadata = _metadata()

    assert extract_metadata(embed_metadata(metadata)) == metadata


def test_embedded_comment_is_invisible_html() -> None:
    rendered = embed_metadata(_metadata())

    assert rendered.startswith("<!--")
    assert rendered.endswith("-->")


def test_marker_can_appear_mid_body() -> None:
    metadata = _metadata(environment="production")

    body = f"prefix text {embed_metadata(metadata)} suffix text"

    assert extract_metadata(body) == metadata


def test_no_marker_returns_none() -> None:
    assert extract_metadata("Just a regular pull request body.") is None
    assert extract_metadata("") is None


def test_malformed_json_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_metadata(f"{MARKER} {{not json}} -->")


def test_valid_json_wrong_shape_raises() -> None:
    with pytest.raises(ValidationError):
        extract_metadata(f'{MARKER} {{"client_payload": {{"nope": true}}}} -->')
