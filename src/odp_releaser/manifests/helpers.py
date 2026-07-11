from tempfile import NamedTemporaryFile

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


def open_for_editing(manifest_text: str) -> Processor:
    with NamedTemporaryFile(mode="w+", encoding="utf-8") as f:
        f.write(manifest_text)
        f.seek(0)
        yaml_data, doc_loaded = Parsers.get_yaml_data(yaml, yamlpath_logger, f.name)

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


def apply_set_templates(
    processor: Processor,
    set_paths: dict[str, str],
    payload: ClientPayload,
    commit_message: list[str],
) -> None:
    """Apply each ``manifest.set`` path/value onto ``processor``.

    Values are templated with ``{new_tag}``, ``{git_sha}`` and ``{digest}``
    drawn from ``payload``. A missing template variable raises a ``KeyError``
    wrapped with the offending path and value to aid debugging. Each applied
    change is appended to ``commit_message``.
    """
    for set_path, value in set_paths.items():
        try:
            formatted_value = value.format(**payload.value_format_kwargs())
        except KeyError as e:
            msg = f"Error setting value for path '{set_path}' with value '{value}'"
            raise KeyError(msg) from e
        message = set_value(processor, set_path, formatted_value, mustexist=True)
        commit_message.append(f"  - {message}")
