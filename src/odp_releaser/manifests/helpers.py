from pathlib import Path

from yamlpath import Processor
from yamlpath.common import Parsers
from yamlpath.enums import YAMLValueFormats

from odp_releaser.logger import logger
from odp_releaser.schemas.client_payload import ClientPayload
from odp_releaser.yamlpath_logger import YamlPathLoggerAdapter

yaml = Parsers.get_yaml_editor(explicit_start=False)
yamlpath_logger = YamlPathLoggerAdapter(logger)


class ManifestLoadError(Exception):
    """Raised when a manifest's YAML cannot be loaded."""


def read_manifest_text(manifest_path: Path) -> tuple[str, str]:
    """A manifest's text with ``\\n`` line endings, plus the endings it really uses.

    Returned as a pair so a caller that writes the file back can restore what
    was there. Reading is done with newline translation disabled so the
    original convention is observable, then normalised to ``\\n`` because
    that's what the manifest engines emit -- comparing their output against
    an untranslated CRLF original would report every file as changed.

    Without this, a bump rewrote every line of a CRLF manifest (the engines
    emit ``\\n``), and on Windows the default ``newline=None`` on write turned
    an LF manifest into CRLF -- either way a one-tag change became a
    whole-file diff.
    """
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        raw = handle.read()
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), newline


def write_manifest_text(manifest_path: Path, text: str, newline: str) -> None:
    """Write a manifest back with the line endings :func:`read_manifest_text` saw.

    ``newline`` is always passed explicitly (never left as ``None``) so the
    platform's ``os.linesep`` can't silently rewrite every line ending on
    Windows.
    """
    manifest_path.write_text(text, encoding="utf-8", newline=newline)


def resolve_manifest_path(config_path: Path, manifest_path: Path) -> Path:
    """Resolve a manifest's ``path`` field against its config file's directory.

    Paths inside an image manifest config are documented (on ``bump-images
    --config-path``) as relative to the config file's own parent directory,
    not the process's current working directory. ``bump_images`` (which
    reads and writes the file) and the config validator (which checks it
    ahead of time) both resolve every manifest path through this exact
    function -- if the two ever resolved a path differently, the validator
    would be checking a different file than the one ``bump_images`` actually
    reads and writes, silently making its checks meaningless.
    """
    return (config_path.parent / manifest_path).resolve()


def display_manifest_path(manifest_path: Path) -> Path:
    """A resolved manifest path, shown relative to the cwd when possible.

    Keeps commit messages and logs readable (and stable across CI runners
    with different absolute checkout paths) by referring to manifests
    relative to the working directory. Falls back to the resolved path
    itself when it isn't under the cwd (e.g. a manifest path that escapes
    the repository).
    """
    try:
        return manifest_path.relative_to(Path.cwd())
    except ValueError:
        return manifest_path


def open_for_editing(manifest_text: str) -> Processor:
    yaml_data, doc_loaded = Parsers.get_yaml_data(
        yaml, yamlpath_logger, manifest_text, literal=True
    )
    if not doc_loaded:
        msg = "Unable to load manifest YAML"
        raise ManifestLoadError(msg)
    return Processor(yamlpath_logger, yaml_data)


def set_value(
    processor: Processor,
    path: str,
    value: str,
    mustexist: bool = True,
    value_format: YAMLValueFormats = YAMLValueFormats.DEFAULT,
) -> str:
    logger.debug(f"Nodes for path {path}: {list(processor.get_nodes(path))}")
    processor.set_value(path, value, mustexist=mustexist, value_format=value_format)
    return f"Set value for path {path} to {value}"


_QUOTED_PATH_SUFFIXES = ("tag", "digest")


def _value_format_for_path(set_path: str) -> YAMLValueFormats:
    """Force double-quoting for tag/digest-like ``manifest.set`` paths.

    Bare tags and digests (e.g. ``1.2.3``, ``sha256:abc...``) can be
    misparsed as non-string YAML scalars, so those paths are always
    quoted. Other ``set`` paths (e.g. kustomize ``resources`` refs) keep
    whatever quote style is already in the file.
    """
    last_segment = set_path.rsplit("/", 1)[-1]
    if last_segment.lower() in _QUOTED_PATH_SUFFIXES:
        return YAMLValueFormats.DQUOTE
    return YAMLValueFormats.DEFAULT


def apply_set_templates(
    processor: Processor,
    set_paths: dict[str, str],
    payload: ClientPayload,
    deployed_name: str,
    commit_message: list[str],
) -> None:
    """Apply each ``manifest.set`` path/value onto ``processor``.

    Values are templated with ``{new_tag}``, ``{git_sha}`` and ``{digest}``
    drawn from ``payload``, plus ``{deployed_image}`` -- the name the
    manifests actually deploy from (``ImageConfig.deployed_as`` when set,
    otherwise the payload's own ``image_name``; see
    :meth:`ImageConfig.deployed_name`, the single place that rule is
    computed). ``{deployed_image}`` lets a ``file_manifests`` entry write
    e.g. ``"{deployed_image}@{digest}"`` instead of a hand-typed mirror
    registry path. A missing template variable raises a ``KeyError`` wrapped
    with the offending path and value to aid debugging. Each applied change
    is appended to ``commit_message``.
    """
    format_kwargs = payload.value_format_kwargs() | {"deployed_image": deployed_name}
    for set_path, value in set_paths.items():
        try:
            formatted_value = value.format(**format_kwargs)
        except KeyError as e:
            msg = f"Error setting value for path '{set_path}' with value '{value}'"
            raise KeyError(msg) from e
        value_format = _value_format_for_path(set_path)
        message = set_value(
            processor,
            set_path,
            formatted_value,
            mustexist=True,
            value_format=value_format,
        )
        commit_message.append(f"  - {message}")
