from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
import typer.testing
from inline_snapshot import snapshot
from pydantic import ValidationError

from odp_releaser.main import app
from odp_releaser.make_payload import UnsupportedEventTypeError, build_payload
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.schemas.github_context import PrMerge, parse_pr_merge

EVENT_DATA = Path(__file__).parent / "event_data"
CLIENT_PAYLOAD_EXAMPLES = (
    Path(__file__).parent.parent / "src" / "odp_releaser" / "client_payload"
)

COMMON_KWARGS = {
    "image_name": "climatology_py_dash",
    "tag": "3f52d83",
    "digest": "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
    "image_repository": "ghcr.io/gulfofmaine",
    "repo": "gulfofmaine/climatology_py_dash",
    "actor": "abkfenris",
    "run_id": "29046325966",
    "server_url": "https://github.com",
}


# --- build_payload: push --------------------------------------------------


def test_build_payload_push_with_pr() -> None:
    pr = parse_pr_merge((EVENT_DATA / "pr_push" / "pr-merge.json").read_text())
    assert pr is not None

    payload = build_payload(
        **COMMON_KWARGS,
        ref_name="dump-actions-events",
        sha="3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
        event_name="push",
        event_data={},
        pr=pr,
    )

    assert payload.model_dump(mode="json") == snapshot(
        {
            "image_name": "climatology_py_dash",
            "digest": "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
            "tag": "3f52d83",
            "git_sha": "3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
            "image_ref": "ghcr.io/gulfofmaine/climatology_py_dash@sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
            "source": {
                "event": "push",
                "ref": "dump-actions-events",
                "url": "https://github.com/gulfofmaine/climatology_py_dash/pull/96",
                "run_url": "https://github.com/gulfofmaine/climatology_py_dash/actions/runs/29046325966",
                "actor": "abkfenris",
                "release": None,
                "pr": {
                    "number": 96,
                    "title": "Dump Github Actions events",
                    "url": "https://github.com/gulfofmaine/climatology_py_dash/pull/96",
                },
            },
            "repo": "gulfofmaine/climatology_py_dash",
        }
    )

    round_tripped = ClientPayload.model_validate_json(payload.model_dump_json())
    assert round_tripped == payload
    assert payload.new_tag() == "3f52d83"


def test_build_payload_push_without_pr() -> None:
    payload = build_payload(
        **COMMON_KWARGS,
        ref_name="dump-actions-events",
        sha="3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
        event_name="push",
        event_data={},
        pr=None,
    )

    assert payload.model_dump(mode="json") == snapshot(
        {
            "image_name": "climatology_py_dash",
            "digest": "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
            "tag": "3f52d83",
            "git_sha": "3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
            "image_ref": "ghcr.io/gulfofmaine/climatology_py_dash@sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
            "source": {
                "event": "push",
                "ref": "dump-actions-events",
                "url": "https://github.com/gulfofmaine/climatology_py_dash/commit/3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
                "run_url": "https://github.com/gulfofmaine/climatology_py_dash/actions/runs/29046325966",
                "actor": "abkfenris",
                "release": None,
                "pr": None,
            },
            "repo": "gulfofmaine/climatology_py_dash",
        }
    )

    round_tripped = ClientPayload.model_validate_json(payload.model_dump_json())
    assert round_tripped == payload
    assert payload.new_tag() == "3f52d83"


# --- build_payload: release ------------------------------------------------


def test_build_payload_release() -> None:
    event_data = json.loads((EVENT_DATA / "release" / "event.json").read_text())

    payload = build_payload(
        **COMMON_KWARGS,
        ref_name="main",
        sha="78a7de370c4f8a6d0f0f1a49a59f15fcd703c92b",
        event_name="release",
        event_data=event_data,
        pr=None,
    )

    assert payload.model_dump(mode="json") == snapshot(
        {
            "image_name": "climatology_py_dash",
            "digest": "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
            "tag": "3f52d83",
            "git_sha": "78a7de370c4f8a6d0f0f1a49a59f15fcd703c92b",
            "image_ref": "ghcr.io/gulfofmaine/climatology_py_dash@sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
            "source": {
                "event": "release",
                "ref": "v1.2.3",
                "url": "https://github.com/gulfofmaine/climatology_py_dash/releases/tag/v1.2.3",
                "run_url": "https://github.com/gulfofmaine/climatology_py_dash/actions/runs/29046325966",
                "actor": "abkfenris",
                "release": {
                    "tag": "v1.2.3",
                    "name": "v1.2.3",
                    "url": "https://github.com/gulfofmaine/climatology_py_dash/releases/tag/v1.2.3",
                },
                "pr": None,
            },
            "repo": "gulfofmaine/climatology_py_dash",
        }
    )

    round_tripped = ClientPayload.model_validate_json(payload.model_dump_json())
    assert round_tripped == payload
    assert payload.new_tag() == "v1.2.3"


# --- build_payload: workflow_dispatch --------------------------------------


def test_build_payload_workflow_dispatch() -> None:
    event_data = json.loads(
        (EVENT_DATA / "workflow_dispatch" / "event.json").read_text()
    )

    payload = build_payload(
        **COMMON_KWARGS,
        ref_name="main",
        sha="5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c",
        event_name="workflow_dispatch",
        event_data=event_data,
        pr=None,
    )

    assert payload.model_dump(mode="json") == snapshot(
        {
            "image_name": "climatology_py_dash",
            "digest": "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
            "tag": "3f52d83",
            "git_sha": "5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c",
            "image_ref": "ghcr.io/gulfofmaine/climatology_py_dash@sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
            "source": {
                "event": "workflow_dispatch",
                "ref": "main",
                "url": "https://github.com/gulfofmaine/climatology_py_dash/commit/5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c",
                "run_url": "https://github.com/gulfofmaine/climatology_py_dash/actions/runs/29046325966",
                "actor": "abkfenris",
                "release": None,
                "pr": None,
            },
            "repo": "gulfofmaine/climatology_py_dash",
        }
    )

    round_tripped = ClientPayload.model_validate_json(payload.model_dump_json())
    assert round_tripped == payload
    assert payload.new_tag() == "3f52d83"


# --- build_payload: unsupported event --------------------------------------


def test_build_payload_unsupported_event_raises() -> None:
    with pytest.raises(UnsupportedEventTypeError) as excinfo:
        build_payload(
            **COMMON_KWARGS,
            ref_name="main",
            sha="deadbeef",
            event_name="pull_request",
            event_data={},
            pr=None,
        )

    assert "pull_request" in str(excinfo.value)
    assert excinfo.value.event_name == "pull_request"


# --- digest validation -------------------------------------------------------


def test_build_payload_rejects_digest_with_repository_prefix() -> None:
    kwargs = {
        **COMMON_KWARGS,
        "digest": (
            "repo@sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b"
        ),
    }

    with pytest.raises(ValidationError) as excinfo:
        build_payload(
            **kwargs,
            ref_name="main",
            sha="5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c",
            event_name="workflow_dispatch",
            event_data={},
            pr=None,
        )

    assert "Strip any repository prefix" in str(excinfo.value)


def test_build_payload_rejects_non_digest_string() -> None:
    kwargs = {**COMMON_KWARGS, "digest": "latest"}

    with pytest.raises(ValidationError) as excinfo:
        build_payload(
            **kwargs,
            ref_name="main",
            sha="5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c",
            event_name="workflow_dispatch",
            event_data={},
            pr=None,
        )

    assert "Strip any repository prefix" in str(excinfo.value)


# --- CLI ---------------------------------------------------------------


def test_make_payload_cli_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_pr = PrMerge(
        number=96,
        title="Dump Github Actions events",
        html_url="https://github.com/gulfofmaine/climatology_py_dash/pull/96",
    )

    def fake_pr_for_commit(repo: str, sha: str, token: str) -> PrMerge | None:
        assert repo == "gulfofmaine/climatology_py_dash"
        assert sha == "3f52d837400fbc2b6719c6d70bf60f46c46bdfc9"
        assert token == "gh_token"
        return fake_pr

    monkeypatch.setattr("odp_releaser.make_payload.pr_for_commit", fake_pr_for_commit)

    event_path = tmp_path / "event.json"
    event_path.write_text("{}")

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        [
            "make-payload",
            "climatology_py_dash",
            "3f52d83",
            "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
        ],
        env={
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "gulfofmaine/climatology_py_dash",
            "GITHUB_ACTOR": "abkfenris",
            "GITHUB_RUN_ID": "29046325966",
            "GITHUB_REF_NAME": "dump-actions-events",
            "GITHUB_SHA": "3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
            "GITHUB_TOKEN": "gh_token",
        },
    )

    assert result.exit_code == 0, result.output
    payload = ClientPayload.model_validate_json(result.stdout)
    assert payload.image_name == "climatology_py_dash"
    assert payload.source.event == "push"
    assert payload.source.pr is not None
    assert payload.source.pr.number == 96
    assert payload.image_ref == (
        "ghcr.io/gulfofmaine/climatology_py_dash@sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b"
    )


def test_make_payload_cli_push_without_token_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_pr_for_commit(
        repo: str,  # noqa: ARG001
        sha: str,  # noqa: ARG001
        token: str,  # noqa: ARG001
    ) -> PrMerge | None:
        msg = "pr_for_commit should not be called without a token"
        raise AssertionError(msg)

    monkeypatch.setattr("odp_releaser.make_payload.pr_for_commit", fail_pr_for_commit)

    event_path = tmp_path / "event.json"
    event_path.write_text("{}")

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        [
            "make-payload",
            "climatology_py_dash",
            "3f52d83",
            "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
        ],
        env={
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "gulfofmaine/climatology_py_dash",
            "GITHUB_ACTOR": "abkfenris",
            "GITHUB_RUN_ID": "29046325966",
            "GITHUB_REF_NAME": "dump-actions-events",
            "GITHUB_SHA": "3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
        },
    )

    assert result.exit_code == 0, result.output
    payload = ClientPayload.model_validate_json(result.stdout)
    assert payload.source.pr is None


def test_make_payload_cli_release(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text((EVENT_DATA / "release" / "event.json").read_text())

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        [
            "make-payload",
            "climatology_py_dash",
            "3f52d83",
            "sha256:2a4b6c8d0e1f3a5b7c9d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b",
        ],
        env={
            "GITHUB_EVENT_NAME": "release",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "gulfofmaine/climatology_py_dash",
            "GITHUB_ACTOR": "abkfenris",
            "GITHUB_RUN_ID": "29046325966",
            "GITHUB_REF_NAME": "main",
            "GITHUB_SHA": "78a7de370c4f8a6d0f0f1a49a59f15fcd703c92b",
        },
    )

    assert result.exit_code == 0, result.output
    payload = ClientPayload.model_validate_json(result.stdout)
    assert payload.source.event == "release"
    assert payload.new_tag() == "v1.2.3"


def test_make_payload_cli_rejects_malformed_digest(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text("{}")

    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        [
            "test",
            "make-payload",
            "climatology_py_dash",
            "3f52d83",
            "gmri/neracoos-climatology-py-dash@sha256:"
            "041d1a8c2ef53044d3ea25d686e92e3ba02b25e8c9dbe1aa2d0d4ef27089ed39",
        ],
        env={
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "gulfofmaine/climatology_py_dash",
            "GITHUB_ACTOR": "abkfenris",
            "GITHUB_RUN_ID": "29046325966",
            "GITHUB_REF_NAME": "dump-actions-events",
            "GITHUB_SHA": "3f52d837400fbc2b6719c6d70bf60f46c46bdfc9",
        },
    )

    assert result.exit_code == 1
    assert result.exception is None or isinstance(
        result.exception, (typer.Exit, SystemExit)
    )
    output = result.output or result.stderr
    assert "Strip any repository prefix" in output


# --- stable interface: canned examples still validate -----------------------


@pytest.mark.parametrize(
    "example_name", ["push.json", "release.json", "workflow_dispatch.json"]
)
def test_client_payload_examples_still_validate(example_name: str) -> None:
    text = (CLIENT_PAYLOAD_EXAMPLES / example_name).read_text()
    payload = ClientPayload.model_validate_json(text)
    assert payload.source.event in {"push", "release", "workflow_dispatch"}
