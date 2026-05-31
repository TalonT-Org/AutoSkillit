"""Architectural invariant: no registered backend may raise NotImplementedError."""

import ast
import inspect

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_no_not_implemented_error_in_registered_backends() -> None:
    """No method on any BACKEND_REGISTRY class should raise NotImplementedError.

    CodingAgentBackend is a typing.Protocol, not an ABC — there are no legitimate
    abstract stubs. Any raise NotImplementedError in a registered backend is a bug.
    """
    from autoskillit.execution.backends import BACKEND_REGISTRY

    violations: list[str] = []
    for name, cls in BACKEND_REGISTRY.items():
        source = inspect.getsource(cls)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise):
                continue
            exc = node.exc
            if (
                isinstance(exc, ast.Call)
                and isinstance(exc.func, ast.Name)
                and exc.func.id == "NotImplementedError"
            ) or (isinstance(exc, ast.Name) and exc.id == "NotImplementedError"):
                violations.append(f"{name}:{node.lineno}")

    assert not violations, (
        f"Registered backends must not raise NotImplementedError "
        f"(CodingAgentBackend is a Protocol, not an ABC): {violations}"
    )
