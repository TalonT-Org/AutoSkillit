"""Verification tests for BackendContractBase ABC mixin."""

from __future__ import annotations

import abc
import dataclasses
import typing
from unittest.mock import create_autospec

import pytest

from autoskillit.core import BackendCapabilities, CodingAgentBackend
from tests.execution.backends.test_backend_contract_base import BackendContractBase

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class _ConcreteContract(BackendContractBase):
    def __init__(self, backend: CodingAgentBackend) -> None:
        self._backend = backend

    def make_backend(self) -> CodingAgentBackend:
        return self._backend


def _make_stub_backend(**cap_overrides: bool) -> CodingAgentBackend:
    backend = create_autospec(CodingAgentBackend, instance=True)
    caps = dataclasses.replace(BackendCapabilities(), **cap_overrides)
    backend.capabilities = caps
    return backend


class TestBackendContractBaseStructure:
    def test_is_abc_subclass(self) -> None:
        assert issubclass(BackendContractBase, abc.ABC)

    def test_no_test_prefix_in_class_name(self) -> None:
        assert not BackendContractBase.__name__.startswith("Test")

    def test_make_backend_is_abstract(self) -> None:
        assert "make_backend" in BackendContractBase.__abstractmethods__

    def test_make_backend_return_annotation(self) -> None:
        hints = typing.get_type_hints(BackendContractBase.make_backend)
        assert hints["return"] is CodingAgentBackend


class TestRequireCapability:
    def test_raises_for_unknown_field(self) -> None:
        contract = _ConcreteContract(_make_stub_backend())
        with pytest.raises(AttributeError, match="nonexistent_field"):
            contract._require_capability("nonexistent_field")

    def test_raises_for_non_bool_field(self) -> None:
        contract = _ConcreteContract(_make_stub_backend())
        with pytest.raises(AttributeError, match="not a bool capability"):
            contract._require_capability("min_version")

    def test_skips_when_false(self) -> None:
        contract = _ConcreteContract(_make_stub_backend(channel_b_capable=False))
        with pytest.raises(pytest.skip.Exception):
            contract._require_capability("channel_b_capable")

    def test_no_skip_when_true(self) -> None:
        contract = _ConcreteContract(_make_stub_backend(channel_b_capable=True))
        result = contract._require_capability("channel_b_capable")
        assert result is None


class TestModuleConstraints:
    def test_no_autouse_fixture_no_constants_no_headless_import(self) -> None:
        import inspect

        from tests.execution.backends import test_backend_contract_base as mod

        source = inspect.getsource(mod)
        assert "autouse" not in source
        assert "_CONTRACT_CLEAN_ENV_VARS" not in source
        assert "_HEADLESS_EXCLUSIVE_VARS" not in source

    def test_no_test_functions_in_module(self) -> None:
        from tests.execution.backends import test_backend_contract_base as mod

        test_attrs = [a for a in dir(mod) if a.startswith("test_")]
        assert test_attrs == [], f"Found test functions: {test_attrs}"

    def test_pytestmark_correct(self) -> None:
        from tests.execution.backends import test_backend_contract_base as mod

        assert mod.pytestmark == [
            pytest.mark.layer("execution"),
            pytest.mark.small,
        ]
