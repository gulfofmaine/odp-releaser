from __future__ import annotations

import logging

import pytest
from ruamel.yaml.compat import StringIO
from yamlpath import Processor
from yamlpath.common import Parsers

from odp_releaser.yamlpath_logger import YamlPathLoggerAdapter


@pytest.fixture
def stdlib_logger() -> logging.Logger:
    logger = logging.getLogger("odp-releaser-test")
    logger.setLevel(logging.DEBUG)
    return logger


def test_debug_with_prefix_does_not_raise_and_includes_prefix(
    stdlib_logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = YamlPathLoggerAdapter(stdlib_logger)
    caplog.set_level(logging.DEBUG, logger="odp-releaser-test")

    adapter.debug("something happened", prefix="Processor::get_nodes: ")

    assert any(
        "Processor::get_nodes: " in record.message for record in caplog.records
    )
    assert any("something happened" in record.message for record in caplog.records)


def test_debug_with_data_does_not_raise_and_includes_data(
    stdlib_logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = YamlPathLoggerAdapter(stdlib_logger)
    caplog.set_level(logging.DEBUG, logger="odp-releaser-test")

    adapter.debug("dumping data", data={"key": "value"})

    assert any("key" in record.message and "value" in record.message for record in caplog.records)


def test_critical_always_raises_system_exit_with_given_code(
    stdlib_logger: logging.Logger,
) -> None:
    adapter = YamlPathLoggerAdapter(stdlib_logger)

    with pytest.raises(SystemExit) as excinfo:
        adapter.critical("boom", exit_code=42)

    assert excinfo.value.code == 42


def test_critical_default_exit_code_is_one(stdlib_logger: logging.Logger) -> None:
    adapter = YamlPathLoggerAdapter(stdlib_logger)

    with pytest.raises(SystemExit) as excinfo:
        adapter.critical("boom")

    assert excinfo.value.code == 1


def test_error_without_exit_code_does_not_raise(stdlib_logger: logging.Logger) -> None:
    adapter = YamlPathLoggerAdapter(stdlib_logger)

    adapter.error("recoverable error")


def test_error_with_exit_code_raises_system_exit(stdlib_logger: logging.Logger) -> None:
    adapter = YamlPathLoggerAdapter(stdlib_logger)

    with pytest.raises(SystemExit) as excinfo:
        adapter.error("fatal error", exit_code=7)

    assert excinfo.value.code == 7


def test_processor_with_adapter_and_debug_enabled_does_not_raise_type_error(
    stdlib_logger: logging.Logger,
) -> None:
    """Regression test for TypeError from stdlib logger not accepting yamlpath kwargs."""
    yaml_source = """\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
images:
  - name: some-image
    newTag: old-tag
"""
    yaml = Parsers.get_yaml_editor()
    adapter = YamlPathLoggerAdapter(stdlib_logger)

    yaml_data = yaml.load(StringIO(yaml_source))

    processor = Processor(adapter, yaml_data)

    # Should not raise TypeError: got an unexpected keyword argument 'prefix'
    processor.set_value("images[0].newTag", "new-tag")

    assert yaml_data["images"][0]["newTag"] == "new-tag"
