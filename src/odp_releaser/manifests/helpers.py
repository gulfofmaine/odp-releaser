from tempfile import NamedTemporaryFile

from yamlpath import Processor
from yamlpath.common import Parsers

from odp_releaser.logger import logger
from odp_releaser.yamlpath_logger import YamlPathLoggerAdapter

yaml = Parsers.get_yaml_editor()
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
    processor: Processor, path: str, value: str, mustexist: bool = True
) -> str:
    logger.warning(f"Nodes for path {path}: {list(processor.get_nodes(path))}")
    processor.set_value(path, value, mustexist=mustexist)
    return f"Set value for path {path} to {value}"
