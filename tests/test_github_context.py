from __future__ import annotations

from pathlib import Path

import pytest
from inline_snapshot import snapshot

from odp_releaser.schemas.github_context import (
    PrMerge,
    PushEvent,
    ReleaseEvent,
    WorkflowDispatchEvent,
    parse_pr_merge,
)

EVENT_DATA = Path(__file__).parent / "event_data"


def test_pr_push_pr_merge() -> None:
    text = (EVENT_DATA / "pr_push" / "pr-merge.json").read_text()
    pr_merge = parse_pr_merge(text)

    assert pr_merge is not None
    assert pr_merge.model_dump() == snapshot(
        {
            "number": 96,
            "title": "Dump Github Actions events",
            "html_url": "https://github.com/gulfofmaine/climatology_py_dash/pull/96",
        }
    )


def test_push_pr_merge_pr_merge() -> None:
    text = (EVENT_DATA / "push_pr_merge" / "pr-merge.json").read_text()
    pr_merge = parse_pr_merge(text)

    assert pr_merge is not None
    assert pr_merge.model_dump() == snapshot(
        {
            "number": 96,
            "title": "Dump Github Actions events",
            "html_url": "https://github.com/gulfofmaine/climatology_py_dash/pull/96",
        }
    )


@pytest.mark.parametrize("text", ["", "   ", "\n", "\t \n"])
def test_parse_pr_merge_empty(text: str) -> None:
    assert parse_pr_merge(text) is None


def test_pr_push_event() -> None:
    text = (EVENT_DATA / "pr_push" / "event.json").read_text()
    push_event = PushEvent.model_validate_json(text)

    assert push_event.model_dump() == snapshot(
        {
            "ref": "refs/heads/dump-actions-events",
            "after": "3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
            "head_commit": {
                "id": "3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
                "url": "https://github.com/gulfofmaine/climatology_py_dash/commit/3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
            },
        }
    )


def test_push_pr_merge_event() -> None:
    text = (EVENT_DATA / "push_pr_merge" / "event.json").read_text()
    push_event = PushEvent.model_validate_json(text)

    assert push_event.model_dump() == snapshot(
        {
            "ref": "refs/heads/main",
            "after": "78a7de370c4f8a6d0f0f1a49a59f15fcd703c92b",
            "head_commit": {
                "id": "78a7de370c4f8a6d0f0f1a49a59f15fcd703c92b",
                "url": "https://github.com/gulfofmaine/climatology_py_dash/commit/78a7de370c4f8a6d0f0f1a49a59f15fcd703c92b",
            },
        }
    )


def test_release_event() -> None:
    text = (EVENT_DATA / "release" / "event.json").read_text()
    release_event = ReleaseEvent.model_validate_json(text)

    assert release_event.model_dump() == snapshot(
        {
            "action": "published",
            "release": {
                "tag_name": "v1.2.3",
                "name": "v1.2.3",
                "html_url": "https://github.com/gulfofmaine/climatology_py_dash/releases/tag/v1.2.3",
            },
        }
    )


def test_workflow_dispatch_event() -> None:
    text = (EVENT_DATA / "workflow_dispatch" / "event.json").read_text()
    workflow_dispatch_event = WorkflowDispatchEvent.model_validate_json(text)

    assert workflow_dispatch_event.model_dump() == snapshot(
        {
            "ref": "refs/heads/main",
            "inputs": {"image_tag": "v1.2.3", "environment": "production"},
        }
    )


def test_pr_merge_ignores_extra_fields() -> None:
    pr_merge = PrMerge.model_validate(
        {
            "number": 1,
            "title": "Some title",
            "html_url": "https://github.com/example/example/pull/1",
            "state": "open",
            "draft": False,
        }
    )

    assert pr_merge.number == 1
