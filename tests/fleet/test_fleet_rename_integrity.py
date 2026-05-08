import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def test_fleet_menu_tools_importable_from_core() -> None:
    from autoskillit.core import FLEET_MENU_TOOLS

    assert FLEET_MENU_TOOLS == ("dispatch_food_truck", "record_gate_dispatch")


def test_campaign_summary_importable_from_fleet() -> None:
    from autoskillit.fleet import CampaignSummary, parse_campaign_summary

    assert CampaignSummary is not None
    assert callable(parse_campaign_summary)


def test_fleet_error_code_class_exists() -> None:
    from autoskillit.core import FleetErrorCode

    assert "fleet_parallel_refused" in FleetErrorCode._value2member_map_


def test_fleet_error_code_class_gone() -> None:
    with pytest.raises(ImportError):
        from autoskillit.core import FranchiseErrorCode  # noqa: F401


def test_fleet_lock_protocol_exists() -> None:
    from autoskillit.core import FleetLock

    assert FleetLock is not None


def test_fleet_error_helper_exists() -> None:
    from autoskillit.core import fleet_error

    assert callable(fleet_error)


def test_fleet_error_helper_gone() -> None:
    with pytest.raises(ImportError):
        from autoskillit.core import franchise_error  # noqa: F401


def test_feature_registry_fleet_tool_tags() -> None:
    from autoskillit.core import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["fleet"].tool_tags == frozenset({"fleet"})


def test_feature_registry_fleet_import_package() -> None:
    from autoskillit.core import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["fleet"].import_package == "autoskillit.fleet"


def test_feature_registry_franchise_entry_gone() -> None:
    from autoskillit.core import FEATURE_REGISTRY

    assert "franchise" not in FEATURE_REGISTRY


def test_session_type_no_franchise() -> None:
    from autoskillit.core.types._type_enums import SessionType

    assert not hasattr(SessionType, "FRANCHISE")


def test_session_type_fleet_canonical() -> None:
    from autoskillit.core.types._type_enums import SessionType

    assert SessionType.FLEET.value == "fleet"


def test_hook_registry_fleet_dispatch_guard() -> None:
    from autoskillit.hook_registry import HOOK_REGISTRY

    scripts = [s for hd in HOOK_REGISTRY for s in hd.scripts]
    assert "guards/fleet_dispatch_guard.py" in scripts
    assert "franchise_dispatch_guard.py" not in scripts


def test_retired_basenames_includes_franchise_guard() -> None:
    from autoskillit.hook_registry import RETIRED_SCRIPT_BASENAMES

    assert "franchise_dispatch_guard.py" in RETIRED_SCRIPT_BASENAMES


def test_fleet_config_class_exists() -> None:
    from autoskillit.config import FleetConfig

    assert FleetConfig is not None


def test_franchise_config_class_gone() -> None:
    with pytest.raises(ImportError):
        from autoskillit.config import FranchiseConfig  # noqa: F401


def test_tool_context_fleet_lock_field() -> None:
    from dataclasses import fields

    from autoskillit.pipeline.context import ToolContext

    field_names = {f.name for f in fields(ToolContext)}
    assert "fleet_lock" in field_names
    assert "franchise_lock" not in field_names
