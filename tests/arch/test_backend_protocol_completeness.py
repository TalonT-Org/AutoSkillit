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


def test_coding_agent_backend_protocol_includes_validate_skill_content():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "validate_skill_content"), (
        "CodingAgentBackend protocol must define validate_skill_content"
    )
    assert callable(getattr(CodingAgentBackend, "validate_skill_content")), (
        "validate_skill_content must be callable"
    )


def test_all_backends_implement_validate_skill_content():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "validate_skill_content"), (
            f"{name} backend must implement validate_skill_content"
        )
        assert callable(getattr(cls, "validate_skill_content")), (
            f"{name} backend validate_skill_content must be callable"
        )


def test_coding_agent_backend_protocol_includes_version():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "version"), (
        "CodingAgentBackend protocol must define version"
    )
    assert callable(getattr(CodingAgentBackend, "version")), "version must be callable"


def test_all_backends_implement_version():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "version"), f"{name} backend must implement version"
        assert callable(getattr(cls, "version")), f"{name} backend version must be callable"


def test_coding_agent_backend_protocol_includes_list_plugins():
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "list_plugins"), (
        "CodingAgentBackend protocol must define list_plugins"
    )
    assert callable(getattr(CodingAgentBackend, "list_plugins")), "list_plugins must be callable"


def test_all_backends_implement_list_plugins():
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "list_plugins"), f"{name} backend must implement list_plugins"
        assert callable(getattr(cls, "list_plugins")), (
            f"{name} backend list_plugins must be callable"
        )
