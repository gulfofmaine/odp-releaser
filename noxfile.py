#!/usr/bin/env -S uv run --script

# /// script
# dependencies = ["jsonschema", "nox>=2025.2.9"]
# [tool.uv]
# exclude-newer = "2026-07-24T00:00:00Z"
# ///

"""Nox runner."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import nox

DIR = Path(__file__).parent.resolve()
PROJECT = nox.project.load_toml()

# Committed JSON Schema filename -> the `generate-config schema` subcommand
# that produces it. Used by the `schemas` session below.
SCHEMAS = {
    "image_manifest.schema.json": "image-manifest",
    "deploy_targets.schema.json": "deploy-targets",
}


nox.needs_version = ">=2025.2.9"
nox.options.default_venv_backend = "uv|virtualenv"


@nox.session
def lint(session: nox.Session) -> None:
    """
    Run the linter.
    """
    session.install("prek")
    session.run(
        "prek", "run", "--all-files", "--show-diff-on-failure", *session.posargs
    )


@nox.session
def pylint(session: nox.Session) -> None:
    """
    Run Pylint.
    """
    # This needs to be installed into the package environment, and is slower
    # than a pre-commit check
    session.install("-e.", "pylint>=3.2")
    session.run("pylint", "odp_releaser", *session.posargs)


@nox.session
def tests(session: nox.Session) -> None:
    """
    Run the unit and regular tests.
    """
    test_deps = nox.project.dependency_groups(PROJECT, "test")
    session.install("-e.", *test_deps)
    session.run("pytest", *session.posargs)


@nox.session(default=False)
def schemas(session: nox.Session) -> None:
    """
    Regenerate the committed JSON Schemas in schemas/, or --check them.

    The schemas are generated from the pydantic config models, so they go
    stale whenever a model changes. Pass --check to verify they are current
    (and are valid JSON Schema) without writing anything; the test suite
    asserts the same thing, so CI needs no separate run.
    """
    # jsonschema is a script dependency of this noxfile (see the PEP 723 block
    # above), but it's imported here rather than at module scope so the other
    # sessions still load under a runner that doesn't provide it -- CI runs
    # `uvx nox -s pylint`, which imports this file with only nox installed.
    from jsonschema import Draft202012Validator  # noqa: PLC0415

    session.install("-e.")
    check = "--check" in session.posargs

    stale: list[str] = []
    for filename, command in SCHEMAS.items():
        generated = session.run(
            "odp-releaser", "generate-config", "schema", command, silent=True
        )
        if not isinstance(generated, str):  # pragma: no cover - dry run
            continue

        target = DIR / "schemas" / filename
        if check:
            if target.read_text() != generated:
                stale.append(filename)
        elif target.read_text() == generated:
            print(f"{filename} is up to date")
        else:
            target.write_text(generated)
            print(f"{filename} regenerated")

        Draft202012Validator.check_schema(json.loads(generated))

    if stale:
        session.error(
            f"Stale schemas: {', '.join(stale)}. Regenerate with `nox -s schemas`."
        )


@nox.session(reuse_venv=True, default=False)
def docs(session: nox.Session) -> None:
    """
    Make or serve the docs. Pass --non-interactive to avoid serving.
    """

    doc_deps = nox.project.dependency_groups(PROJECT, "docs")
    session.install("-e.", *doc_deps)

    if session.interactive:
        session.run("zensical", "serve", *session.posargs)
    else:
        session.run("zensical", "build", "--clean", *session.posargs)


@nox.session(reuse_venv=True, default=False)
def docs_clean(_) -> None:
    """
    Clean the built documentation.
    """
    build_path = DIR.joinpath("site")
    if build_path.exists():
        shutil.rmtree(build_path)


@nox.session(default=False)
def build(session: nox.Session) -> None:
    """
    Build an SDist and wheel.
    """

    build_path = DIR.joinpath("build")
    if build_path.exists():
        shutil.rmtree(build_path)

    session.install("build")
    session.run("python", "-m", "build")


if __name__ == "__main__":
    nox.main()
