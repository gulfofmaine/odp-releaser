See the [Scientific Python Developer Guide][spc-dev-intro] for a detailed
description of best practices for developing scientific packages.

[spc-dev-intro]: https://learn.scientific-python.org/development/

This project was generated from
[scientific-python/cookie](https://github.com/scientific-python/cookie) with
copier; `.copier-answers.yml` records the answers, and `copier update` pulls in
upstream template changes.

<!-- --8<-- [start:contributing] -->

## Tooling

Three tools do most of the work here:

- [**uv**](https://docs.astral.sh/uv/) manages the environment and the lockfile.
  It can be installed
  [a variety of ways](https://docs.astral.sh/uv/getting-started/installation/).
- [**nox**](https://nox.thea.codes/) is the task runner — every check CI runs
  has a session. Use `./noxfile.py` to run it without installing, or
  `uv tool install nox`.
- [**prek**](https://prek.j178.dev/) runs the git hooks (a Rust drop-in for
  pre-commit). `uv tool install prek`, or `brew install prek` on macOS.

There is also a [dev container](../.devcontainer/devcontainer.json) if you'd
rather not set any of that up locally.

## Quick development

The fastest way to start with development is to use nox. Running `nox` with no
arguments lints and tests using every installed version of Python on your
system, skipping ones that are not installed. Nox handles everything for you,
including setting up a temporary virtual environment for each run.

You can also run specific sessions:

```console
$ nox -s lint          # run pre-commit hooks against all files
$ nox -s tests         # the pytest suite
$ nox -s test_locale   # the pytest suite under LC_ALL=C, which catches Windows-ish bugs
$ nox -s pylint        # pylint
$ nox -s docs          # build and serve the docs
$ nox -s docs_clean    # remove the built site/
$ nox -s schemas       # regenerate the committed JSON Schemas
$ nox -s build         # make an SDist and wheel
```

## Setting up a development environment manually

You can set up a development environment by running:

```bash
uv sync
```

That installs the package plus the `test` and `docs` dependency groups.

## Pre-commit (via Prek)

You should prepare prek, which will help you by checking that commits pass
required checks:

```bash
uv tool install prek # or brew install prek on macOS
prek install # Will install a pre-commit hook into the git repo
```

You can also/alternatively run `prek run` (changes only) or
`prek run --all-files` to check even without installing the hook.

Alongside the usual formatters and linters, the hooks run this project's own
`validate-image-manifest` and `validate-deploy-targets` checks against the test
fixtures, `zizmor` against the workflows, and `regenerate-json-schemas`.

## Testing

Use pytest to run the unit checks:

```bash
uv run pytest
```

## Coverage

Use pytest-cov to generate coverage reports:

```bash
uv run pytest --cov=odp-releaser
```

## JSON Schemas

The files in `schemas/` are generated from the pydantic config models, so they
go stale whenever a model changes. Regenerate them with:

```bash
nox -s schemas
```

`nox -s schemas -- --check` verifies they are current without writing anything.
The test suite asserts the same thing, so a stale schema fails CI, and the
`regenerate-json-schemas` pre-commit hook keeps them in step automatically.

## Building docs

You can build and serve the docs using:

```bash
nox -s docs
```

You can build the docs only with:

```bash
nox -s docs --non-interactive
```

The docs are built with [Zensical](https://zensical.org/), configured in
`zensical.toml`. Pages pull content from the code in three ways, so a build
failure is often a code problem rather than a prose one:

- `mkdocstrings` with the `python` handler renders the pydantic config models;
- `mkdocstrings-github` renders the reusable workflows' and composite actions'
  inputs, outputs, and secrets straight from their YAML — so those are edited in
  `.github/`, not in markdown;
- `markdown-exec` runs the fenced `python`/`bash` blocks marked `exec="on"` at
  build time, which is how the example configs and rendered comment templates
  stay accurate.

The Documentation workflow builds the site on every pull request, and deploys to
GitHub Pages only from `main`.

<!-- --8<-- [end:contributing] -->
