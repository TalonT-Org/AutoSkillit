"""Tests for fleet error envelope registry and constructor.

Group R: FleetErrorCode enum, FLEET_ERROR_CODES frozenset, and fleet_error() helper.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"

# Matches error code strings in the fleet/l2/dispatch/cleanup namespace
_FLEET_CODE_PATTERN = re.compile(r"^(fleet_|l2_|dispatch_|cleanup_)")


class TestFleetErrorCodeEnum:
    def test_fleet_error_code_enum_has_expected_codes(self):
        from autoskillit.core import FleetErrorCode

        expected_values = {
            "fleet_parallel_refused",
            "fleet_unknown_ingredient",
            "fleet_recipe_not_found",
            "fleet_invalid_recipe_kind",
            "fleet_hard_refusal_headless",
            "fleet_manifest_missing",
            "fleet_manifest_corrupted",
            "fleet_lock_not_initialized",
            "l2_timeout",
            "l2_no_result_block",
            "l2_parse_failed",
            "l2_startup_or_crash",
            "dispatch_budget_exceeded",
            "quota_exhausted",
            "cleanup_failed",
        }
        assert {c.value for c in FleetErrorCode} == expected_values

    def test_fleet_error_codes_frozenset_matches_enum(self):
        from autoskillit.core import FLEET_ERROR_CODES, FleetErrorCode

        assert FLEET_ERROR_CODES == frozenset(FleetErrorCode)

    def test_fleet_error_code_values_are_snake_case(self):
        from autoskillit.core import FleetErrorCode

        snake_case = re.compile(r"^[a-z][a-z0-9_]*$")
        for code in FleetErrorCode:
            assert snake_case.match(code.value), f"{code!r} is not snake_case"

    def test_fleet_error_code_enum_re_exported_from_core(self):
        from autoskillit.core import FleetErrorCode  # noqa: F401

        assert FleetErrorCode is not None


class TestFleetErrorHelper:
    def test_fleet_error_rejects_unregistered_code(self):
        from autoskillit.core import fleet_error

        with pytest.raises(ValueError, match="Unregistered fleet error code"):
            fleet_error("bogus_code", "msg")

    def test_fleet_error_returns_valid_json_envelope(self):
        from autoskillit.core import FleetErrorCode, fleet_error

        result = fleet_error(FleetErrorCode.L2_TIMEOUT, "timed out")
        data = json.loads(result)
        assert set(data.keys()) >= {"success", "error", "user_visible_message", "details"}
        assert data["success"] is False
        assert data["error"] == "l2_timeout"
        assert data["user_visible_message"] == "timed out"

    def test_fleet_error_details_default_none(self):
        from autoskillit.core import FleetErrorCode, fleet_error

        result = json.loads(fleet_error(FleetErrorCode.L2_TIMEOUT, "msg"))
        assert result["details"] is None

    def test_fleet_error_details_json_serializable(self):
        from autoskillit.core import FleetErrorCode, fleet_error

        result = json.loads(
            fleet_error(
                FleetErrorCode.L2_TIMEOUT,
                "msg",
                details={"key": [1, 2]},
            )
        )
        assert result["details"] == {"key": [1, 2]}

        with pytest.raises(TypeError):
            fleet_error(
                FleetErrorCode.L2_TIMEOUT,
                "msg",
                details={"fn": lambda: 0},  # type: ignore[arg-type]
            )

    def test_fleet_error_re_exported_from_pipeline(self):
        import importlib

        pipeline = importlib.import_module("autoskillit.pipeline")
        assert hasattr(pipeline, "fleet_error")
        assert pipeline.fleet_error is not None


class TestFleetErrorASTScan:
    def _find_raw_fleet_error_dumps(self, path: Path) -> list[str]:
        """Find raw json.dumps({..., 'error': '<franchise_code>'}) calls in a file.

        Returns a list of violation descriptions. Empty = clean.
        """
        src = path.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return []

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Check if this is a json.dumps(...) call
            func = node.func
            is_json_dumps = (
                isinstance(func, ast.Attribute)
                and func.attr == "dumps"
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
            )
            if not is_json_dumps:
                continue
            if not node.args:
                continue
            dict_arg = node.args[0]
            if not isinstance(dict_arg, ast.Dict):
                continue
            # Look for "error" key with a franchise-style string literal value
            for key, value in zip(dict_arg.keys, dict_arg.values):
                if not isinstance(key, ast.Constant) or key.value != "error":
                    continue
                if not isinstance(value, ast.Constant):
                    continue
                error_val = str(value.value)
                if _FLEET_CODE_PATTERN.match(error_val):
                    violations.append(
                        f"{path.name}:{node.lineno}: raw json.dumps with error={error_val!r}"
                    )
        return violations

    def test_all_fleet_errors_use_registered_code(self):
        all_violations: list[str] = []
        for f in sorted(SRC_ROOT.rglob("*.py")):
            all_violations.extend(self._find_raw_fleet_error_dumps(f))
        assert not all_violations, (
            "Raw json.dumps fleet error patterns found. "
            "Use fleet_error() instead:\n" + "\n".join(all_violations)
        )
