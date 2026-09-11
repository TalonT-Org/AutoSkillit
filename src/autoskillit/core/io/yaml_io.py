"""YAML load/dump helpers extracted from core.io.

Owns the unique-key-rejecting YAML loader and all YAML load/compose/dump helpers.
Centralizes the ``yaml`` import for ``autoskillit.core.io`` — the rest of the io/
sub-package re-uses the public surface here without re-importing pyyaml.
"""

from __future__ import annotations

import os
from typing import Any, TypeGuard

import yaml
from yaml import YAMLError as YAMLError  # explicit re-export for callers and type checkers

try:
    from yaml import CSafeLoader as _Loader
except ImportError:
    _Loader = yaml.SafeLoader  # type: ignore[misc,assignment]


class _UniqueKeyLoader(_Loader):
    """Safe loader that rejects duplicate mapping keys before construction."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)

try:
    from yaml import CDumper as _Dumper
except ImportError:
    from yaml import Dumper as _Dumper  # type: ignore[misc,assignment]


def load_yaml(source: os.PathLike[str] | str) -> Any:
    """Load YAML from a file path or raw string.

    Pass any ``os.PathLike`` (including ``pathlib.Path``) to read from disk,
    or a ``str`` to parse directly. Uses binary mode for portable UTF-8/BOM
    handling when reading from a path.
    """
    if isinstance(source, os.PathLike):
        with open(source, "rb") as fh:
            return yaml.load(fh, Loader=_UniqueKeyLoader)
    return yaml.load(source, Loader=_UniqueKeyLoader)


def compose_yaml(source: str) -> yaml.Node | None:
    """Parse *source* into a mark-annotated YAML node tree (not a data structure).

    Unlike :func:`load_yaml`, retains ``start_mark`` / ``end_mark`` character
    offsets on every node, which the byte-range tracker in
    ``server/tools/_serve_helpers.py`` uses to compute per-step byte spans
    of the original ``content`` text. Returns ``None`` when the source is
    empty (matches :func:`yaml.compose` semantics).
    """
    return yaml.compose(source, Loader=_Loader)


def is_yaml_mapping_node(node: object) -> TypeGuard[yaml.MappingNode]:
    """Return whether *node* is a YAML mapping without leaking the YAML dependency."""
    return isinstance(node, yaml.MappingNode)


def mapping_entry_byte_ranges_from_yaml(
    content: str, mapping_path: tuple[str, ...]
) -> dict[str, tuple[int, int]]:
    """Compute UTF-8 byte ranges for entries under a YAML mapping path.

    Walks the persisted YAML ``content`` field via :func:`compose_yaml` to read
    each selected mapping entry's key/value ``start_mark`` / ``end_mark`` character
    offsets, then converts them to UTF-8 byte offsets so the result can be
    used directly to slice the payload back at the byte level.

    Centralizes the yaml import: this module is the only place in the
    package that imports ``yaml`` directly (REQs in
    ``tests/arch/test_subpackage_isolation_module_boundaries.py::
    test_only_yaml_imports_yaml_directly`` and
    ``tests/core/test_io.py::test_only_yaml_imports_yaml_directly``).
    """
    out: dict[str, tuple[int, int]] = {}
    if not content or not mapping_path:
        return out
    try:
        root = compose_yaml(content)
    except yaml.YAMLError:
        return out
    if not isinstance(root, yaml.MappingNode):
        return out
    current = root
    for segment in mapping_path:
        next_node = None
        for key_node, value_node in current.value:
            if getattr(key_node, "value", None) == segment:
                next_node = value_node
                break
        if not isinstance(next_node, yaml.MappingNode):
            return out
        current = next_node
    for entry_key, entry_value in current.value:
        start_idx = entry_key.start_mark.index
        end_idx = entry_value.end_mark.index
        out[str(entry_key.value)] = (
            len(content[:start_idx].encode("utf-8")),
            len(content[:end_idx].encode("utf-8")),
        )
    return out


def dump_yaml_str(data: Any, **kwargs: Any) -> str:
    """Serialize data to a YAML string.

    Accepts ``yaml.dump`` kwargs (e.g. ``sort_keys=False``,
    ``default_flow_style=False``). Distinct from the removed ``dump_yaml`` which wrote
    to disk.
    """
    kwargs.pop("Dumper", None)
    return yaml.dump(data, Dumper=_Dumper, **kwargs)
