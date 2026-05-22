"""Protocol completeness tests for CodingAgentBackend command builders."""

from __future__ import annotations


def test_coding_agent_backend_protocol_includes_skill_session_cmd():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "build_skill_session_cmd"), (
        "CodingAgentBackend protocol must define build_skill_session_cmd"
    )
    assert callable(getattr(CodingAgentBackend, "build_skill_session_cmd")), (
        "build_skill_session_cmd must be callable"
    )


def test_coding_agent_backend_protocol_includes_food_truck_cmd():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "build_food_truck_cmd"), (
        "CodingAgentBackend protocol must define build_food_truck_cmd"
    )
    assert callable(getattr(CodingAgentBackend, "build_food_truck_cmd")), (
        "build_food_truck_cmd must be callable"
    )


def test_all_backends_implement_skill_session_cmd():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "build_skill_session_cmd"), (
            f"{name} backend must implement build_skill_session_cmd"
        )
        assert callable(getattr(cls, "build_skill_session_cmd")), (
            f"{name} backend build_skill_session_cmd must be callable"
        )


def test_all_backends_implement_food_truck_cmd():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "build_food_truck_cmd"), (
            f"{name} backend must implement build_food_truck_cmd"
        )
        assert callable(getattr(cls, "build_food_truck_cmd")), (
            f"{name} backend build_food_truck_cmd must be callable"
        )
