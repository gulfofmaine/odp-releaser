"""Render example pydantic model instances as commented YAML.

Field descriptions become comments before their keys and model docstrings
become comments at the top of their mapping blocks. Each comment is emitted
only the first time its model class (docstrings) or ``(model class, field
name)`` pair (descriptions) is rendered, so repeated models stay readable.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

if TYPE_CHECKING:
    from collections.abc import Sequence

# Model classes (docstrings) and (model class, field name) pairs (descriptions)
# whose comments have already been emitted during a single render.
_SeenComments = set[object]


def _field_default(field: Any) -> object:  # noqa: ANN401
    """Best-effort default value for a pydantic ``FieldInfo``."""
    if field.default_factory is not None:
        return field.default_factory()
    if field.default is not PydanticUndefined:
        return field.default
    return PydanticUndefined


def _bare_string(model: BaseModel) -> str | None:
    """Return the shorthand string for ``model`` when it collapses to one.

    A model opts in via the ``_shorthand_field`` class attribute. It collapses
    when every field other than that one is still at its default value, letting
    ``example_yaml`` emit e.g. ``- ../path.yaml`` instead of ``- path: ...``.
    """
    shorthand_field = getattr(type(model), "_shorthand_field", None)
    if shorthand_field is None:
        return None
    for name, field in type(model).model_fields.items():
        if name == shorthand_field:
            continue
        if getattr(model, name) != _field_default(field):
            return None
    return str(getattr(model, shorthand_field))


def _is_empty(value: object) -> bool:
    """Whether a value should be omitted from the generated YAML."""
    if value is None:
        return True
    return isinstance(value, (list, tuple, dict)) and len(value) == 0


def _to_yaml(value: object, key_indent: int, seen: _SeenComments) -> object:
    """Convert ``value`` to a ruamel node.

    ``key_indent`` is the column at which the keys of the produced node would
    appear if it is a mapping, so nested descriptions line up with their keys.
    """
    if isinstance(value, BaseModel):
        bare = _bare_string(value)
        if bare is not None:
            return bare
        return _model_to_map(value, key_indent, seen)
    if isinstance(value, dict):
        cmap = CommentedMap()
        for key, item in value.items():
            cmap[str(key)] = _to_yaml(item, key_indent + 2, seen)
        return cmap
    if isinstance(value, (list, tuple)):
        seq = CommentedSeq()
        for item in value:
            # Sequence items are dumped with the offset==0 style, so an item
            # mapping's keys share the sequence's own key column.
            seq.append(_to_yaml(item, key_indent, seen))
        return seq
    if isinstance(value, Path):
        return str(value)
    return value


def _model_to_map(
    model: BaseModel, key_indent: int, seen: _SeenComments
) -> CommentedMap:
    """Convert a pydantic model to a ``CommentedMap`` with description comments.

    Each field's ``description`` is attached as a comment before that key, and
    the model class docstring is attached as a comment at the top of its block.
    Comments already emitted earlier in the render (tracked in ``seen``) are
    skipped so repeated models render plain.
    """
    cls = type(model)
    cmap = CommentedMap()
    fields = cls.model_fields
    for name in fields:
        value = getattr(model, name)
        if _is_empty(value):
            continue
        cmap[name] = _to_yaml(value, key_indent + 2, seen)

    for name, field in fields.items():
        if name in cmap and field.description and (cls, name) not in seen:
            seen.add((cls, name))
            cmap.yaml_set_comment_before_after_key(
                name, before=field.description, indent=key_indent
            )

    doc = cls.__doc__
    if doc and cls not in seen:
        seen.add(cls)
        cmap.yaml_set_start_comment(doc.strip(), indent=key_indent)
    return cmap


def example_yaml(model: BaseModel | Sequence[BaseModel]) -> str:
    """Serialise a model instance, or a list of them, to commented YAML.

    Walks ``type(model).model_fields`` to attach each field's description as a
    comment before its key and the class docstring at the top of its mapping
    block, recursing into nested models, lists of models, and dict values.
    Repeated models render without comments, so each description and docstring
    appears only once per document.
    """
    seen: _SeenComments = set()
    root: CommentedMap | CommentedSeq
    if isinstance(model, BaseModel):
        root = _model_to_map(model, 0, seen)
    else:
        root = CommentedSeq()
        # The item model's docstring becomes the top-of-file comment rather
        # than a comment indented within the first item.
        doc = type(model[0]).__doc__ if model else None
        if doc:
            seen.add(type(model[0]))
        for item in model:
            # Top-level sequence items place their keys at column 2.
            root.append(_to_yaml(item, 2, seen))
        if doc:
            root.yaml_set_start_comment(doc.strip(), indent=0)
    yaml = YAML()
    yaml.indent(mapping=2, sequence=2, offset=0)
    stream = io.StringIO()
    yaml.dump(root, stream)
    return stream.getvalue()
