"""Typed callables for _import_and_call type-coercion tests (Step 1a)."""

from __future__ import annotations

from pathlib import Path


def _typed_callable(name: str, count: int, ratio: float = 1.0) -> dict:
    assert isinstance(name, str), f"name should be str, got {type(name)}"
    assert isinstance(count, int), f"count should be int, got {type(count)}"
    assert isinstance(ratio, float), f"ratio should be float, got {type(ratio)}"
    return {"name": name, "count": count, "ratio": ratio}


def _str_only_param(value: str) -> dict:
    assert isinstance(value, str), f"value should be str, got {type(value)}"
    return {"value": value}


def _int_param(value: int) -> dict:
    assert isinstance(value, int), f"value should be int, got {type(value)}"
    return {"value": value}


def _str_optional_param(value: str = "default") -> dict:
    return {"value": value}


def _str_path_param(path: str) -> Path:
    return Path(path)
