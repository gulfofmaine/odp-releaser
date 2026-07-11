from __future__ import annotations

from typing import TYPE_CHECKING

import typer.testing

from odp_releaser.main import app
from odp_releaser.notify import load_targets
from odp_releaser.schemas.dispatch import EXAMPLE_TARGETS
from odp_releaser.schemas.example_yaml import example_yaml

if TYPE_CHECKING:
    from pathlib import Path


def test_example_yaml_handles_top_level_list_of_models() -> None:
    text = example_yaml(EXAMPLE_TARGETS)

    # The DeployTarget docstring is the top-of-file comment.
    assert text.startswith(
        "# A repository that should receive a ``repository_dispatch`` event."
    )
    # Field descriptions are attached to the first item only.
    assert text.count("# Owner of the deploy repository") == 1
    assert text.count("# Name of the deploy repository") == 1
    assert text.count("owner:") == 2
    assert text.count("repo:") == 2


def test_generate_deploy_targets_round_trips_through_notify_validation(
    tmp_path: Path,
) -> None:
    runner = typer.testing.CliRunner()
    result = runner.invoke(app, ["generate-config", "deploy-targets"])

    assert result.exit_code == 0

    # The printed YAML must parse with the same validation notify uses on
    # .github/deploy_targets.yaml.
    targets_path = tmp_path / "deploy_targets.yaml"
    targets_path.write_text(result.stdout)
    targets = load_targets(targets_path)

    assert targets == EXAMPLE_TARGETS
    assert targets[0].event_type == "image-published"
    assert targets[1].event_type == "custom-event"
