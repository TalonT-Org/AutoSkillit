from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core import DirectInstall
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

CAPABILITY_METHOD_MAP: dict[str, tuple[str, dict]] = {
    "food_truck_capable": (
        "build_food_truck_cmd",
        {
            "orchestrator_prompt": "x",
            "plugin_source": DirectInstall(plugin_dir=Path("/tmp")),
            "cwd": "/tmp",
            "completion_marker": "%%X%%",
        },
    ),
    "session_resume_capable": (
        "build_resume_cmd",
        {"resume_session_id": "x", "prompt": "x"},
    ),
}


@pytest.mark.parametrize("backend_name", list(BACKEND_REGISTRY))
class TestCapabilityMethodConsistency:
    def test_true_bool_capabilities_have_non_raising_implementations(
        self, backend_name: str
    ) -> None:
        backend = BACKEND_REGISTRY[backend_name]()
        for cap_name, (method_name, kwargs) in CAPABILITY_METHOD_MAP.items():
            if not getattr(backend.capabilities, cap_name):
                continue
            method = getattr(backend, method_name)
            try:
                method(**kwargs)
            except (RuntimeError, NotImplementedError) as exc:
                pytest.fail(f"{backend_name}.{method_name}() raised {type(exc).__name__}: {exc}")

    def test_inspector_capable_is_false(self, backend_name: str) -> None:
        backend = BACKEND_REGISTRY[backend_name]()
        assert backend.capabilities.inspector_capable is False


class TestWriteDetectionStrategyRouting:
    def test_extract_file_changes_routes_by_strategy_not_by_name(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.execution.headless._headless_evidence import (
            _extract_file_changes,
        )

        backend = CodexBackend()
        original_caps = backend.capabilities

        # tool_names strategy: returns [] without parsing
        tool_names_caps = replace(original_caps, write_detection_strategy="tool_names")
        monkeypatch.setattr(
            CodexBackend,
            "capabilities",
            property(lambda self: tool_names_caps),
        )
        assert _extract_file_changes("", backend) == []

        # file_changes strategy: parses stdout for file_changes key
        file_changes_caps = replace(
            original_caps,
            write_detection_strategy="file_changes",
        )
        monkeypatch.setattr(
            CodexBackend,
            "capabilities",
            property(lambda self: file_changes_caps),
        )
        mock_result = Mock()
        mock_result.raw = {"file_changes": ["src/foo.py"]}
        mock_parser = Mock()
        mock_parser.parse_stdout.return_value = mock_result
        monkeypatch.setattr(CodexBackend, "result_parser", lambda self: mock_parser)
        assert _extract_file_changes("dummy-stdout", backend) == ["src/foo.py"]
