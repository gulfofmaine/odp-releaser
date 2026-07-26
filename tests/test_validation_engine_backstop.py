from __future__ import annotations

from odp_releaser.schemas.manifest_config import ImageConfig
from odp_releaser.validation.engine_backstop import representative_event


def test_representative_event_defaults_to_push_with_no_events_restriction() -> None:
    """A config matching every event has no single "own" event to synthesize --
    ``push`` is an arbitrary but representative non-release choice, since
    ``ClientPayload.new_tag()`` only branches on release vs. everything else.
    """
    assert representative_event(ImageConfig()) == "push"


def test_representative_event_uses_the_configs_own_first_event() -> None:
    """A release-only config must be synthesized as a release: `new_tag()`
    would otherwise read ``tag`` instead of ``source.ref``, the one branch
    that actually matters here.
    """
    assert representative_event(ImageConfig(events=["release"])) == "release"
