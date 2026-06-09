from __future__ import annotations

import ast
import typing
from pathlib import Path

import pytest

from autoskillit.core import BackendCapabilities, CodingAgentBackend
from tests.execution.backends.test_backend_contract_base import BackendContractBase

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

MIXIN_SOURCE = Path(__file__).parent / "test_backend_contract_base.py"


class _StubBackend:
    """Minimal stub satisfying CodingAgentBackend.capabilities for testing."""

    def __init__(self, capabilities: BackendCapabilities) -> None:
        self._caps = capabilities

    @property
    def name(self) -> str:
        return "stub"

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._caps


class ConcreteContract(BackendContractBase):
    """Concrete subclass for testing the ABC mixin."""

    def __init__(self, caps: BackendCapabilities | None = None) -> None:
        self._caps = caps or BackendCapabilities()

    def make_backend(self) -> CodingAgentBackend:
        return _StubBackend(self._caps)  # type: ignore[return-value]


class TestBackendContractBaseStructure:
    def test_mixin_is_importable_and_is_abc(self) -> None:
        import abc

        assert issubclass(BackendContractBase, abc.ABC)

    def test_make_backend_is_abstract(self) -> None:
        assert getattr(BackendContractBase.make_backend, "__isabstractmethod__", False)

    def test_make_backend_return_annotation(self) -> None:
        hints = typing.get_type_hints(BackendContractBase.make_backend)
        assert hints["return"] is CodingAgentBackend

    def test_pytest_does_not_collect_mixin(self) -> None:
        assert not BackendContractBase.__name__.startswith("Test")

    def test_pytestmark_correct(self) -> None:
        import tests.execution.backends.test_backend_contract_base as mod

        assert mod.pytestmark == [
            pytest.mark.layer("execution"),
            pytest.mark.small,
        ]

    def test_no_test_functions_in_mixin_file(self) -> None:
        tree = ast.parse(MIXIN_SOURCE.read_text())
        test_funcs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        ]
        assert test_funcs == [], f"Found test functions: {test_funcs}"

    def test_no_autouse_fixture_in_mixin_file(self) -> None:
        source = MIXIN_SOURCE.read_text()
        assert "autouse" not in source

    def test_no_headless_exclusive_vars_import(self) -> None:
        tree = ast.parse(MIXIN_SOURCE.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                source_text = ast.get_source_segment(MIXIN_SOURCE.read_text(), node)
                assert "_HEADLESS_EXCLUSIVE_VARS" not in (source_text or "")


class TestRequireCapability:
    def test_raises_on_unknown_field(self) -> None:
        contract = ConcreteContract()
        with pytest.raises(AttributeError, match="nonexistent_field"):
            contract._require_capability("nonexistent_field")

    def test_skips_when_capability_is_false(self) -> None:
        contract = ConcreteContract(BackendCapabilities(channel_b_capable=False))
        with pytest.raises(pytest.skip.Exception):
            contract._require_capability("channel_b_capable")

    def test_does_not_skip_when_capability_is_true(self) -> None:
        contract = ConcreteContract(BackendCapabilities(supports_tool_list_changed=True))
        result = contract._require_capability("supports_tool_list_changed")
        assert result is None
