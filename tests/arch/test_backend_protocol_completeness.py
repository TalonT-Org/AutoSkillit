"""Protocol completeness tests for CodingAgentBackend command builders."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


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


def test_coding_agent_backend_protocol_includes_build_interactive_cmd():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "build_interactive_cmd"), (
        "CodingAgentBackend protocol must define build_interactive_cmd"
    )
    assert callable(getattr(CodingAgentBackend, "build_interactive_cmd")), (
        "build_interactive_cmd must be callable"
    )


def test_all_backends_implement_build_interactive_cmd():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "build_interactive_cmd"), (
            f"{name} backend must implement build_interactive_cmd"
        )
        assert callable(getattr(cls, "build_interactive_cmd")), (
            f"{name} backend build_interactive_cmd must be callable"
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


def test_coding_agent_backend_protocol_includes_validate_session_layout():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "validate_session_layout"), (
        "CodingAgentBackend protocol must define validate_session_layout"
    )
    assert callable(getattr(CodingAgentBackend, "validate_session_layout")), (
        "validate_session_layout must be callable"
    )


def test_all_backends_implement_validate_session_layout():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "validate_session_layout"), (
            f"{name} backend must implement validate_session_layout"
        )
        assert callable(getattr(cls, "validate_session_layout")), (
            f"{name} backend validate_session_layout must be callable"
        )


def test_coding_agent_backend_protocol_includes_ensure_pre_launch():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "ensure_pre_launch"), (
        "CodingAgentBackend protocol must define ensure_pre_launch"
    )
    assert callable(getattr(CodingAgentBackend, "ensure_pre_launch")), (
        "ensure_pre_launch must be callable"
    )


def test_all_backends_implement_ensure_pre_launch():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "ensure_pre_launch"), (
            f"{name} backend must implement ensure_pre_launch"
        )
        assert callable(getattr(cls, "ensure_pre_launch")), (
            f"{name} backend ensure_pre_launch must be callable"
        )


def test_coding_agent_backend_protocol_includes_translate_model():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "translate_model"), (
        "CodingAgentBackend protocol must define translate_model"
    )
    assert callable(getattr(CodingAgentBackend, "translate_model")), (
        "translate_model must be callable"
    )


def test_all_backends_implement_translate_model():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "translate_model"), f"{name} backend must implement translate_model"
        assert callable(getattr(cls, "translate_model")), (
            f"{name} backend translate_model must be callable"
        )


def test_coding_agent_backend_protocol_includes_build_inspector_cmd():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "build_inspector_cmd"), (
        "CodingAgentBackend protocol must define build_inspector_cmd"
    )
    assert callable(getattr(CodingAgentBackend, "build_inspector_cmd")), (
        "build_inspector_cmd must be callable"
    )


def test_all_backends_implement_build_inspector_cmd():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "build_inspector_cmd"), (
            f"{name} backend must implement build_inspector_cmd"
        )
        assert callable(getattr(cls, "build_inspector_cmd")), (
            f"{name} backend build_inspector_cmd must be callable"
        )


def test_coding_agent_backend_protocol_includes_conventions():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "conventions"), (
        "CodingAgentBackend protocol must define conventions"
    )
    assert isinstance(CodingAgentBackend.__dict__["conventions"], property), (
        "conventions must be a property"
    )


def test_coding_agent_backend_protocol_includes_setup_session_dir():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "setup_session_dir"), (
        "CodingAgentBackend protocol must define setup_session_dir"
    )
    assert callable(getattr(CodingAgentBackend, "setup_session_dir")), (
        "setup_session_dir must be callable"
    )
