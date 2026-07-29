"""Paired invariant: the validator's verdict and ``bump_images``'s behavior must agree.

``validation.image_manifest`` predicts, ahead of time, whether ``bump_images``
would apply a config's manifests cleanly. That prediction is itself partly
built by *running* the real manifest engines as a backstop (see that
module's docstring) -- but the two still walk the config independently
(different entry points, different code paths to get to "would this apply"),
so nothing *structurally* guarantees they stay in agreement. This module
exists purely to catch the day one of them drifts from the other.

Of the two directions of the biconditional this asserts, only one is
actually load-bearing:

- **"runtime fails => the validator failed too"** is largely circular:
  ``bump_images`` now runs ``validate_image_configs`` itself as a pre-flight
  (see ``bump_images._preflight``) and refuses to touch a manifest if that
  pre-flight found an error. So of course the runtime "fails" (raises
  ``typer.Exit(1)``) whenever the validator did -- the validator's own
  verdict is *why* it failed. This direction would pass even if the engine
  backstop were deleted entirely.
- **"the validator is clean => a real dry run raises nothing"** is the
  direction that matters. It is the only direction that would catch a new
  engine failure mode the hand-rolled checks (and, if it had a bug, the
  engine backstop) don't model: a config the validator waves through that
  then blows up mid-``bump_images`` with a raw ``KeyError``,
  ``YAMLPathException``, or similar, well past the point of a clean pre-flight.

For every clean case, this module additionally asserts that the dry run
*actually applied* the manifests (``changed == "true"`` in ``GITHUB_OUTPUT``)
rather than merely completing without raising -- proving the engines ran to
completion, not just that nothing happened to touch them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import typer
from test_bump_image import _parse_github_output
from yamlpath.exceptions import YAMLPathException

from odp_releaser.bump_image_tester import (
    EventType,
    load_client_payload,
    set_payload_image,
)
from odp_releaser.bump_images import bump_images
from odp_releaser.validation.image_manifest import validate_image_manifest

if TYPE_CHECKING:
    from collections.abc import Callable

    from odp_releaser.schemas.client_payload import ClientPayload

FIXTURES = Path(__file__).parent / "manifests"
E2E_CONFIG = Path(__file__).parent / "e2e" / "image_manifest.yaml"


def _payload_for(
    image_name: str, event: EventType, *, repo: str | None = None
) -> ClientPayload:
    """A canned payload for ``image_name``/``event``, optionally with ``repo`` replaced.

    The e2e fixture's ``defaults.allowed_source_repos`` only allows
    ``gulfofmaine/odp-releaser`` (see ``tests/e2e/image_manifest.yaml``),
    which none of the canned ``client_payload/*.json`` fixtures have --
    they're all from ``ioos/buoy_retriever``. ``repo`` lets e2e cases satisfy
    that allowlist the same way ``tests/e2e/manifests`` are exercised
    elsewhere (via ``odp-releaser test make-payload --github-repository``),
    without needing a fourth canned payload file just for this.
    """
    payload = load_client_payload(event)
    set_payload_image(image_name, payload)
    if repo is not None:
        payload.repo = repo
    return payload


def _fixed(path: Path) -> Callable[[Path], Path]:
    return lambda _tmp_path: path


def _write_missing_manifest_case(tmp_path: Path) -> Path:
    """Engine failure mode: the manifest file itself doesn't exist."""
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./does-not-exist.yaml
"""
    )
    return config_path


def _write_bad_selector_case(tmp_path: Path) -> Path:
    """Engine failure mode: a ``set`` selector that never resolves."""
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "old"\n'
    )
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - path: ./kustomization.yaml
          set:
            '/nonexistent/path': '{new_tag}'
"""
    )
    return config_path


def _write_bad_placeholder_case(tmp_path: Path) -> Path:
    """Engine failure mode: a ``set`` value with an unknown ``{placeholder}``."""
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    newTag: "old"\n'
    )
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - path: ./kustomization.yaml
          set:
            '/images[name="gmri/app"]/newTag': '{sha}'
"""
    )
    return config_path


def _write_pin_tag_missing_new_tag_case(tmp_path: Path) -> Path:
    """Engine failure mode: ``pin: tag`` but the matching entry has no ``newTag``."""
    (tmp_path / "kustomization.yaml").write_text(
        'images:\n  - name: gmri/app\n    digest: "sha256:' + "0" * 64 + '"\n'
    )
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      kustomize_manifests:
        - ./kustomization.yaml
"""
    )
    return config_path


def _write_helm_dagster_case(tmp_path: Path) -> Path:
    """Clean helm case: a dagster deployment whose image.tag really gets bumped."""
    (tmp_path / "values.yaml").write_text(
        """\
deployments:
  - name: app
    image:
      repository: gmri/app
      tag: "old"
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      helm_charts:
        - path: ./values.yaml
          dagster_user_code: true
""",
        encoding="utf-8",
    )
    return config_path


def _write_helm_no_matching_deployment_case(tmp_path: Path) -> Path:
    """Agreement case where both sides error: no dagster deployment matches.

    ``update_helm_values_with_payload`` sets the tag with ``mustexist=True``,
    which raises when no deployment entry matches -- so the validator must
    call this an error too.
    """
    (tmp_path / "values.yaml").write_text(
        """\
deployments:
  - name: other
    image:
      repository: gmri/somebody-else
      tag: "old"
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      helm_charts:
        - path: ./values.yaml
          dagster_user_code: true
""",
        encoding="utf-8",
    )
    return config_path


def _write_helm_bad_selector_case(tmp_path: Path) -> Path:
    """Engine failure mode in the helm engine: an unresolvable ``set`` selector."""
    (tmp_path / "values.yaml").write_text('image:\n  tag: "old"\n', encoding="utf-8")
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      helm_charts:
        - path: ./values.yaml
          set:
            '/nowhere/tag': '{new_tag}'
""",
        encoding="utf-8",
    )
    return config_path


def _write_file_json_case(tmp_path: Path) -> Path:
    """Clean file-manifest case, exercising the JSON re-serialisation branch."""
    (tmp_path / "deployment.json").write_text(
        '{\n  "spec": {\n    "image": "gmri/app:old"\n  }\n}\n', encoding="utf-8"
    )
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      file_manifests:
        - path: ./deployment.json
          set:
            '/spec/image': 'gmri/app:{new_tag}'
""",
        encoding="utf-8",
    )
    return config_path


def _write_file_bad_placeholder_case(tmp_path: Path) -> Path:
    """Engine failure mode in the file engine: an unknown ``{placeholder}``."""
    (tmp_path / "values.yaml").write_text('image:\n  tag: "old"\n', encoding="utf-8")
    config_path = tmp_path / "image_manifest.yaml"
    config_path.write_text(
        """
images:
  gmri/app:
    - events: [push]
      file_manifests:
        - path: ./values.yaml
          set:
            '/image/tag': '{sha}'
""",
        encoding="utf-8",
    )
    return config_path


@dataclass(frozen=True)
class Case:
    """One corpus entry: a config, the image+event to check it against, and the verdict."""

    case_id: str
    image_name: str
    event: EventType
    expect_error: bool
    build: Callable[[Path], Path]
    repo: str | None = None
    # Whether a clean run must report changed=true. False only where the
    # runtime legitimately applies nothing (helm logs a warning and leaves the
    # file alone when no deployment matches), so "clean" can't imply "applied".
    expect_changed: bool = True


CASES = [
    # --- Real fixtures, dogfooded elsewhere in the suite; image names and
    # events read straight off the fixture files (see tests/manifests/*,
    # tests/e2e/image_manifest.yaml) rather than guessed. ---
    Case(
        "dagster_helm_kustomize-push-clean",
        "gmri/sea-eagle-brown-3crs",
        EventType.push,
        expect_error=False,
        build=_fixed(FIXTURES / "dagster_helm_kustomize" / "image_manifest.yaml"),
    ),
    Case(
        "key_error-push-broken-placeholder",
        "gmri/neracoos-mariners-dashboard",
        EventType.push,
        expect_error=True,
        build=_fixed(FIXTURES / "key_error" / "image_manifest.yaml"),
    ),
    Case(
        "key_error-release-clean",
        "gmri/neracoos-mariners-dashboard",
        EventType.release,
        expect_error=False,
        build=_fixed(FIXTURES / "key_error" / "image_manifest.yaml"),
    ),
    Case(
        "push-release-missing-file",
        "gmri/neracoos-mariners-dashboard",
        EventType.release,
        expect_error=True,
        build=_fixed(FIXTURES / "push" / "image_manifest.yaml"),
    ),
    Case(
        "push-push-clean",
        "gmri/neracoos-mariners-dashboard",
        EventType.push,
        expect_error=False,
        build=_fixed(FIXTURES / "push" / "image_manifest.yaml"),
    ),
    Case(
        "e2e-commit-clean",
        "ghcr.io/gulfofmaine/odp-releaser-e2e-commit",
        EventType.workflow_dispatch,
        expect_error=False,
        build=_fixed(E2E_CONFIG),
        repo="gulfofmaine/odp-releaser",
    ),
    Case(
        "e2e-pr-clean",
        "ghcr.io/gulfofmaine/odp-releaser-e2e-pr",
        EventType.workflow_dispatch,
        expect_error=False,
        build=_fixed(E2E_CONFIG),
        repo="gulfofmaine/odp-releaser",
    ),
    # --- Synthetic tmp_path configs, each isolating one distinct engine
    # failure mode the hand-rolled checks may or may not model. ---
    Case(
        "synthetic-missing-manifest-file",
        "gmri/app",
        EventType.push,
        expect_error=True,
        build=_write_missing_manifest_case,
    ),
    Case(
        "synthetic-selector-does-not-resolve",
        "gmri/app",
        EventType.push,
        expect_error=True,
        build=_write_bad_selector_case,
    ),
    Case(
        "synthetic-bad-placeholder",
        "gmri/app",
        EventType.push,
        expect_error=True,
        build=_write_bad_placeholder_case,
    ),
    Case(
        "synthetic-pin-tag-missing-new-tag",
        "gmri/app",
        EventType.push,
        expect_error=True,
        build=_write_pin_tag_missing_new_tag_case,
    ),
    # --- helm and file engines, so this module covers every engine bump-images
    # can call, not just kustomize. ---
    Case(
        "synthetic-helm-dagster-clean",
        "gmri/app",
        EventType.push,
        expect_error=False,
        build=_write_helm_dagster_case,
    ),
    Case(
        "synthetic-helm-no-matching-deployment",
        "gmri/app",
        EventType.push,
        expect_error=True,
        build=_write_helm_no_matching_deployment_case,
    ),
    Case(
        "synthetic-helm-selector-does-not-resolve",
        "gmri/app",
        EventType.push,
        expect_error=True,
        build=_write_helm_bad_selector_case,
    ),
    Case(
        "synthetic-file-json-clean",
        "gmri/app",
        EventType.push,
        expect_error=False,
        build=_write_file_json_case,
    ),
    Case(
        "synthetic-file-bad-placeholder",
        "gmri/app",
        EventType.push,
        expect_error=True,
        build=_write_file_bad_placeholder_case,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_validator_verdict_matches_runtime_behavior(
    case: Case,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = case.build(tmp_path)
    payload = _payload_for(case.image_name, case.event, repo=case.repo)

    diagnostics = validate_image_manifest(config_path, payload=payload)
    validator_failed = diagnostics.failed()

    output_path = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    runtime_exit_code: int | None = None
    try:
        bump_images(
            config_path=config_path,
            client_payload=payload.model_dump_json(),
            dry_run=True,
        )
    except typer.Exit as exc:
        runtime_exit_code = exc.exit_code

    runtime_failed = runtime_exit_code is not None
    assert runtime_exit_code in (None, 1), (
        f"{case.case_id}: bump_images exited non-zero for an unexpected "
        f"reason: {runtime_exit_code}"
    )

    assert validator_failed == runtime_failed == case.expect_error, (
        f"{case.case_id}: validator failed={validator_failed}, "
        f"runtime failed={runtime_failed}, expected={case.expect_error}\n"
        f"diagnostics:\n{diagnostics.render()}"
    )

    if not validator_failed:
        # The load-bearing direction: a clean validator run must correspond
        # to manifests that were *actually applied*, not merely a dry run
        # that happened not to raise. Every clean corpus entry above targets
        # a manifest whose current on-disk value differs from the canned
        # payload's, so a real bump always changes something.
        # GITHUB_OUTPUT is written as UTF-8 (github_output.py), and a payload
        # can carry non-ASCII (the release fixture's title has an em dash), so
        # never read it with the platform's locale encoding.
        outputs = _parse_github_output(output_path.read_text(encoding="utf-8"))
        expected_changed = "true" if case.expect_changed else "false"
        assert outputs["changed"] == expected_changed, (
            f"{case.case_id}: validator was clean but bump_images reported "
            f"changed={outputs['changed']!r}, expected {expected_changed!r} -- "
            "the engines may not have run to completion"
        )


def test_engine_backstop_fires_when_hand_rolled_checks_miss_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the backstop isn't dead code: it alone must catch this failure.

    Monkeypatches the kustomize engine (as imported into
    ``validation.image_manifest``) to raise a ``YAMLPathException`` on an
    otherwise-clean fixture. None of the hand-rolled checks have any opinion
    about this -- only the engine backstop (validation.image_manifest running
    the real engine and catching what it raises) can turn this into a
    diagnostic, so a passing assertion here means the backstop actually ran.
    """

    def _raise_yamlpath_exception(*_args: object, **_kwargs: object) -> str:
        message = "engine backstop test: contrived kustomize failure"
        raise YAMLPathException(message, "/contrived/path")

    monkeypatch.setattr(
        "odp_releaser.validation.image_manifest.update_kustomize_with_payload",
        _raise_yamlpath_exception,
    )

    diagnostics = validate_image_manifest(
        FIXTURES / "dagster_helm_kustomize" / "image_manifest.yaml"
    )

    assert diagnostics.failed() is True
    assert any(
        "contrived kustomize failure" in d.message for d in diagnostics.diagnostics
    )
