"""Shape rules for anything expected to equal a real image reference.

Shared by two different config values that each only mean something if they
equal a payload's ``image_name``: an ``images:`` key (see
``validation.image_manifest._check_image_name``) and
``ImageConfig.deployed_as`` (see
``validation.deployed_as.check_deployed_as_and_sync``). Kept as one function
rather than two copies so the two checks can't drift on what "shaped like a
real image name" means -- a name failing any of these rules can never equal
what a real payload, ``image.repository``, or ``newName`` carries, so
whatever depends on it (a bump matching, a mirror agreeing) can never work
either.
"""

from __future__ import annotations

INVALID_IMAGE_NAME_CHARS = ("@", ":")


def image_name_shape_problems(name: str) -> list[str]:
    """Problems with ``name``'s shape, or ``[]`` if it's shaped like a real image name.

    Mirrors
    :meth:`~odp_releaser.schemas.client_payload.ClientPayload._validate_image_name`
    (no ``@``/``:``) plus the shape every real image reference otherwise has
    (lowercase, no surrounding whitespace, non-empty) -- ``ClientPayload``
    itself only enforces the former, but a name violating the latter can
    still never equal what a real payload carries.
    """
    problems: list[str] = []
    if not name:
        problems.append("must not be empty")
    if name != name.strip():
        problems.append("must not have leading/trailing whitespace")
    if name != name.lower():
        problems.append("must not contain uppercase characters")
    if any(char in name for char in INVALID_IMAGE_NAME_CHARS):
        problems.append("must not contain '@' or ':'")
    return problems
