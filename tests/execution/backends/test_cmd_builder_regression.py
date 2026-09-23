"""CmdBuilder regression tests: gap areas not exercised by test_cmd_builder.py.

Covers:
- CmdSpec frozen-instance enforcement
- CmdSpec.cmd type contract (all str)
- CmdOrigin field-type shapes
- Direct CmdSpec construction with origin=None default
- Empty-binary ValueError guard on CmdBuilder.__init__
"""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core import CmdOrigin, CmdSpec, PositionalRole
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
        .positional("input.txt", role=PositionalRole.PROMPT)
        .variadic_pair("--extra", "val")
        .build()
    )
    assert all(isinstance(e, str) for e in spec.cmd)


def test_cmd_origin_field_types() -> None:
    spec = (
        CmdBuilder("tool")
        .mode_flag("--verbose")
        .kv_flag("--output", "/tmp")
        .positional("input.txt", role=PositionalRole.PROMPT)
        .variadic_pair("--extra", "val")
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


def test_origin_round_trips_role_bearing_positionals() -> None:
    spec = (
        CmdBuilder("codex")
        .mode_flag("resume")
        .kv_flag("--model", "gpt-synthetic")
        .positional("thread-123", role=PositionalRole.RESUME_TARGET)
        .positional("continue the investigation", role=PositionalRole.PROMPT)
        .variadic_pair("--add-dir", "/workspace")
        .build()
    )

    assert spec.origin is not None
    assert spec.origin.positional == (
        (PositionalRole.RESUME_TARGET, "thread-123"),
        (PositionalRole.PROMPT, "continue the investigation"),
    )
    assert spec.cmd == (
        spec.origin.binary,
        *spec.origin.mode_flags,
        *(value for pair in spec.origin.kv_flags for value in pair),
        *(value for _role, value in spec.origin.positional),
        *(value for pair in spec.origin.variadic_pairs for value in pair),
    )
