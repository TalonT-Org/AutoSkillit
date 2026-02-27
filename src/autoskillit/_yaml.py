"""Thin YAML I/O wrapper — the single point of yaml access in AutoSkillit."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from yaml import YAMLError as YAMLError


def load_yaml(source: os.PathLike[str] | str) -> Any:
    """Load YAML from a file path or raw string.

    Pass any ``os.PathLike`` (including ``pathlib.Path``) to read from disk,
    or a ``str`` to parse directly. Uses binary mode for portable UTF-8/BOM
    handling when reading from a path.
    """
    if isinstance(source, os.PathLike):
        with open(source, "rb") as fh:
            return yaml.safe_load(fh)
    return yaml.safe_load(source)


def dump_yaml(data: Any, path: Path) -> None:
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


def dump_yaml_str(data: Any, **kwargs: Any) -> str:
    """Serialize data to a YAML string.

    Accepts ``yaml.dump`` kwargs (e.g. ``sort_keys=False``,
    ``default_flow_style=False``). Distinct from ``dump_yaml`` which writes
    to disk.
    """
    return yaml.dump(data, **kwargs)
