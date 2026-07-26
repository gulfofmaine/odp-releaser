from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from odp_releaser.bump_image_tester import EventType, load_client_payload
from odp_releaser.report_metadata import (
    MARKER,
    ReportMetadata,
    embed_metadata,
    extract_metadata,
)
from odp_releaser.schemas.manifest_config import ResolvedComment


def _metadata(**kwargs: Any) -> ReportMetadata:
    """Build metadata around a real payload.

    ``kwargs`` is deliberately untyped: the fields it stands in for are a mix
    of strings, an int (``comment_pr_number``) and a model
    (``comment``), and a narrower annotation would just be wrong for some
    caller.
    """
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


def test_comment_settings_round_trip() -> None:
    """The merge-time comment run reads its templates back out of the PR body,
    so they have to survive embedding intact."""
    metadata = _metadata(
        environment="production",
        comment=ResolvedComment(
            enabled=True,
            staged="staged {image_name}",
            deployed="deployed {image_name}",
        ),
        comment_pr_number=142,
    )

    parsed = extract_metadata(embed_metadata(metadata))

    assert parsed is not None
    assert parsed.comment is not None
    assert parsed.comment.staged == "staged {image_name}"
    assert parsed.comment.deployed == "deployed {image_name}"
    assert parsed.comment_pr_number == 142


def test_a_body_from_before_comment_support_still_parses() -> None:
    """Bump pull requests opened by an older odp-releaser carry no comment
    fields; they must keep working for deployment reporting."""
    payload = load_client_payload(EventType.push)
    older = json.dumps(
        {
            "environment": "production",
            "environment_url": "https://mariners.example.com",
            "client_payload": json.loads(payload.model_dump_json()),
        }
    )

    parsed = extract_metadata(f"{MARKER} {older} -->")

    assert parsed is not None
    assert parsed.environment == "production"
    assert parsed.comment is None
    assert parsed.comment_pr_number is None
