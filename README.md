# ODP Releaser

[![Actions Status][actions-badge]][actions-link]
[![Coverage][coverage-badge]][coverage-link]
<!-- [![Documentation Status][rtd-badge]][rtd-link] -->

<!-- [![PyPI version][pypi-version]][pypi-link] -->
<!-- [![PyPI platforms][pypi-platforms]][pypi-link] -->
<!-- [![Conda-Forge][conda-badge]][conda-link] -->

<!-- [![GitHub Discussion][github-discussions-badge]][github-discussions-link] -->

<!-- prettier-ignore-start -->
[actions-badge]:            https://github.com/gulfofmaine/odp-releaser/actions/workflows/ci.yml/badge.svg
[actions-link]:             https://github.com/gulfofmaine/odp-releaser/actions
[conda-badge]:              https://img.shields.io/conda/vn/conda-forge/odp-releaser
[conda-link]:               https://github.com/conda-forge/odp-releaser-feedstock
[github-discussions-badge]: https://img.shields.io/static/v1?label=Discussions&message=Ask&color=blue&logo=github
[github-discussions-link]:  https://github.com/gulfofmaine/odp-releaser/discussions
[pypi-link]:                https://pypi.org/project/odp-releaser/
[pypi-platforms]:           https://img.shields.io/pypi/pyversions/odp-releaser
[pypi-version]:             https://img.shields.io/pypi/v/odp-releaser
[rtd-badge]:                https://readthedocs.org/projects/odp-releaser/badge/?version=latest
[rtd-link]:                 https://odp-releaser.readthedocs.io/en/latest/?badge=latest
[coverage-badge]:           https://codecov.io/github/gulfofmaine/odp-releaser/branch/main/graph/badge.svg
[coverage-link]:            https://codecov.io/github/gulfofmaine/odp-releaser

<!-- prettier-ignore-end -->

ODP Releaser is a Python CLI tool and a set of GitHub Action workflows to help
make deployment of Docker images to private repos more secure.

It takes advantage of GitHub's
[`repository_dispatch` event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#repository_dispatch)
to communicate image changes to the private repos, authenticated with a tightly
scoped GitHub App owned by each deploy org.

In the source repos, a reusable `notify` workflow runs after images are built
and pushed. It builds a `client_payload` and sends a `repository_dispatch` event
to any number of deploy repos listed in the source repo's
`.github/deploy_targets.yaml`.

The deploy repos have a reusable `bump-images` workflow triggered by that
`repository_dispatch` event. It looks up the image against a local
`.github/image_manifest.yaml` config to see what Kustomize, Helm, or other
manifests need to be updated, checks the source repo against an optional
allow-list, and either commits the change directly or opens a pull request.

See the [docs](https://gulfofmaine.github.io/odp-releaser/) for the full
workflow reference, the `client_payload`/manifest config schemas, and how to set
up the GitHub Apps that authenticate the dispatch.
