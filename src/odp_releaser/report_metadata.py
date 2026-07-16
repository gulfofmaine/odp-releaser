"""Machine-readable report metadata embedded in bump pull request bodies.

When ``bump-images`` opens a pull request instead of committing directly, the
deployment reported to the source repo is only ``queued`` — nothing is live
until the PR merges. To let a merge-time workflow finish the report, the bump
embeds everything ``report-deployment`` needs (the client payload plus the
resolved environment name and URL) into the PR body as an invisible HTML
comment. ``peter-evans/create-pull-request`` rewrites the body on every push
to the bump branch, so the metadata always reflects the latest bump.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from odp_releaser.schemas.client_payload import ClientPayload

MARKER = "<!-- odp-releaser:report-deployment"


class ReportMetadata(BaseModel):
    """Everything ``report-deployment`` needs, carried in the bump PR body."""

    environment: str | None = None
    environment_url: str | None = None
    client_payload: ClientPayload


def embed_metadata(metadata: ReportMetadata) -> str:
    """Render ``metadata`` as an invisible HTML comment for the PR body."""
    return f"{MARKER} {metadata.model_dump_json()} -->"


def extract_metadata(pr_body: str) -> ReportMetadata | None:
    """Parse the metadata comment out of ``pr_body``.

    Returns ``None`` when no marker is present (e.g. a pull request that
    wasn't opened by ``bump-images``). Raises ``json.JSONDecodeError`` or
    ``pydantic.ValidationError`` when a marker is present but its content is
    malformed. ``raw_decode`` stops at the end of the JSON object, so the
    trailing ``-->`` never confuses the parse even if the payload itself
    contains that character sequence.
    """
    start = pr_body.find(MARKER)
    if start == -1:
        return None
    raw = pr_body[start + len(MARKER) :].lstrip()
    data, _ = json.JSONDecoder().raw_decode(raw)
    return ReportMetadata.model_validate(data)
