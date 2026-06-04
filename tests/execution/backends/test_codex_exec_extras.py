"""Tests for _codex_exec_extras shared env baseline factory."""

from __future__ import annotations

import ast
import inspect

import pytest

from autoskillit.core import (
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AUTOSKILLIT_APPLICABLE_GUARDS,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    SESSION_TYPE_SKILL,
)
from autoskillit.execution.backends.codex import CodexBackend, _codex_exec_extras

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexExecExtras:
    def test_baseline(self) -> None:
        result = _codex_exec_extras(session_type=SESSION_TYPE_SKILL)
        assert result["AUTOSKILLIT_HEADLESS"] == "1"
        assert result["AUTOSKILLIT_HEADLESS_AUTO_GATE"] == "1"
        assert result["AUTOSKILLIT_SESSION_TYPE"] == SESSION_TYPE_SKILL
        assert AGENT_BACKEND_DYNACONF_ENV_VAR in result
        assert MCP_CLIENT_BACKEND_ENV_VAR in result
        assert FOOD_TRUCK_TOOL_TAGS_ENV_VAR in result

    def test_includes_session_baseline(self) -> None:
        result = _codex_exec_extras(session_type="", include_session_baseline=True)
        assert "MAX_MCP_OUTPUT_TOKENS" in result
        assert "MCP_CONNECTION_NONBLOCKING" in result

    def test_includes_guards(self) -> None:
        result = _codex_exec_extras(
            session_type=SESSION_TYPE_SKILL, applicable_guards=frozenset({"g1", "g2"})
        )
        assert AUTOSKILLIT_APPLICABLE_GUARDS in result
        assert result[AUTOSKILLIT_APPLICABLE_GUARDS] == "g1,g2"

    def test_empty_guards_writes_empty_string(self) -> None:
        result = _codex_exec_extras(session_type=SESSION_TYPE_SKILL, applicable_guards=frozenset())
        assert result[AUTOSKILLIT_APPLICABLE_GUARDS] == ""

    def test_no_guards_omits_key(self) -> None:
        result = _codex_exec_extras(session_type="")
        assert AUTOSKILLIT_APPLICABLE_GUARDS not in result

    def test_includes_agent_backend_flat(self) -> None:
        from autoskillit.core import AGENT_BACKEND_ENV_VAR

        result = _codex_exec_extras(
            session_type=SESSION_TYPE_SKILL, include_agent_backend_flat=True
        )
        assert AGENT_BACKEND_ENV_VAR in result


_BASELINE_KEYS = {
    "AUTOSKILLIT_HEADLESS",
    "AUTOSKILLIT_HEADLESS_AUTO_GATE",
    AGENT_BACKEND_DYNACONF_ENV_VAR,
}


class TestNoRawHeadlessExtrasDict:
    pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

    def test_no_raw_headless_extras_dict_in_codex_backend(self) -> None:
        source = inspect.getsource(CodexBackend)
        tree = ast.parse(source)

        class_body = tree.body[0]
        assert isinstance(class_body, ast.ClassDef)

        for func_node in ast.walk(class_body):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func_node):
                if not isinstance(node, ast.Dict):
                    continue
                str_keys = set()
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        str_keys.add(k.value)
                    elif isinstance(k, ast.Attribute) and isinstance(k.attr, str):
                        str_keys.add(k.attr)
                if _BASELINE_KEYS <= str_keys:
                    raise AssertionError(
                        f"Raw baseline headless extras dict literal found in "
                        f"CodexBackend.{func_node.name}. Use _codex_exec_extras() instead."
                    )
