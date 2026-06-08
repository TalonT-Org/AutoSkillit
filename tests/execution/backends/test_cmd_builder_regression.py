"""CmdBuilder regression: frozen-instance, type contracts, origin defaults, empty-binary."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core import CmdOrigin, CmdSpec
from autoskillit.execution.backends._cmd_builder import CmdBuilder

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_cmd_spec_frozen_raises_on_assign() -> None:
    spec = CmdBuilder("tool").build()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.cmd = ()  # type: ignore[misc]


def test_cmd_spec_cmd_all_strings() -> None:
    spec = (
        CmdBuilder("tool")
        .mode_flag("--verbose")
        .kv_flag("--output", "/tmp")
        .positional("input.txt")
        .variadic_pair("--env", "FOO=bar")
        .build()
    )
    assert all(isinstance(e, str) for e in spec.cmd)


def test_cmd_origin_field_types() -> None:
    spec = (
        CmdBuilder("tool")
        .mode_flag("--verbose")
        .kv_flag("--output", "/tmp")
        .positional("input.txt")
        .variadic_pair("--env", "FOO=bar")
        .build()
    )
    assert spec.origin is not None
    assert isinstance(spec.origin, CmdOrigin)
    assert isinstance(spec.origin.binary, str)
    assert isinstance(spec.origin.mode_flags, tuple)
    assert isinstance(spec.origin.kv_flags, tuple)
    assert isinstance(spec.origin.positional, tuple)
    assert isinstance(spec.origin.variadic_pairs, tuple)


def test_cmd_spec_direct_construction_origin_none() -> None:
    spec = CmdSpec(cmd=("tool",), env={})
    assert spec.origin is None


def test_empty_binary_raises_value_error() -> None:
    with pytest.raises(ValueError):
        CmdBuilder("")
