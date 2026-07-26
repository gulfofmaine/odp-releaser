from __future__ import annotations

import io

from ruamel.yaml import YAML

from odp_releaser.validation.ruamel_lines import line_for_index, line_for_key


def _round_trip_load(text: str) -> object:
    return YAML().load(io.StringIO(text))


# --- line_for_key --------------------------------------------------------------


def test_line_for_key_finds_the_1_based_source_line() -> None:
    data = _round_trip_load("a: 1\nb: 2\n")
    assert line_for_key(data, "b") == 2


def test_line_for_key_returns_none_for_a_non_mapping() -> None:
    """A list (or any non-``Mapping``) has no keys to look up at all."""
    assert line_for_key([1, 2, 3], 0) is None


def test_line_for_key_returns_none_for_a_key_missing_from_lc_data() -> None:
    data = _round_trip_load("a: 1\n")
    assert line_for_key(data, "missing") is None


# --- line_for_index --------------------------------------------------------------


def test_line_for_index_finds_the_1_based_source_line() -> None:
    data = _round_trip_load("- a\n- b\n")
    assert line_for_index(data, 1) == 2


def test_line_for_index_returns_none_for_a_non_list() -> None:
    """A dict (or any non-``list``) has no indices to look up at all."""
    assert line_for_index({"a": 1}, 0) is None


def test_line_for_index_returns_none_for_a_plain_list_with_no_lc_info() -> None:
    """A plain ``list`` passes the ``list`` check but has no ruamel ``.lc`` data,
    since it wasn't produced by a round-trip load -- the same degradation a
    non-round-trip loader's list would hit.
    """
    assert line_for_index([1, 2, 3], 0) is None


def test_line_for_index_returns_none_for_an_index_missing_from_lc_data() -> None:
    data = _round_trip_load("- a\n")
    assert line_for_index(data, 5) is None
