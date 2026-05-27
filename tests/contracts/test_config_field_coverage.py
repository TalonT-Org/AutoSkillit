"""REQ-CONFIG-001: every sub-config dataclass field must be referenced in from_dynaconf.

Finding 9.1 — gate that prevents silent omissions when new fields are added to any
*Config dataclass in settings.py without being handled by _build_subconfig.
"""

import dataclasses

import pytest

import autoskillit.config.settings as settings_mod
from autoskillit.config.settings import AutomationConfig

_SUBCONFIG_DATACLASSES = [
    cls
    for cls in vars(settings_mod).values()
    if isinstance(cls, type)
    and dataclasses.is_dataclass(cls)
    and cls is not AutomationConfig
    and not cls.__dataclass_params__.frozen  # type: ignore[attr-defined]
]


@pytest.mark.parametrize("dc", _SUBCONFIG_DATACLASSES, ids=lambda c: c.__name__)
def test_every_subconfig_field_referenced_in_from_dynaconf(dc: type) -> None:
    """REQ-CONFIG-001: every dataclass field declared in any *Config
    dataclass must be handled by _build_subconfig.

    Calls _build_subconfig with a section dict containing all default values
    and verifies every field is populated on the result.
    """
    from autoskillit.config.settings import (
        _SECTION_BUILDERS,
        _build_subconfig,
        _field_defaults,
    )

    section_name = None
    for f in dataclasses.fields(AutomationConfig):
        if f.default_factory is not dataclasses.MISSING and f.default_factory is dc:
            section_name = f.name
            break
    if section_name is None or section_name in _SECTION_BUILDERS:
        pytest.skip(f"{dc.__name__} uses a custom builder")

    defaults = _field_defaults(dc)
    result = _build_subconfig(dc, dict(defaults), section_name)

    missing = [f.name for f in dataclasses.fields(dc) if not hasattr(result, f.name)]
    assert not missing, (
        f"{dc.__name__} fields not populated by _build_subconfig: {missing}. "
        f"Check _coerce_value handles the field's type annotation."
    )
