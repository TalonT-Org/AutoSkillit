"""Launch authority shard export and compatibility-facade contracts."""

from importlib import import_module

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_AUTHORITY_EXPORTS = {
    "LaunchSurface",
    "BackendAuthorityTier",
    "LaunchValueSourceKind",
    "BackendAuthority",
    "LaunchValueSource",
    "ModelPinResolution",
    "ProviderBinding",
    "LaunchFallbackRoute",
}


def test_launch_authority_exports_and_facades_share_objects() -> None:
    authority = import_module("autoskillit.core.types._type_launch_authority")
    legacy = import_module("autoskillit.core.types._type_launch")
    core_types = import_module("autoskillit.core.types")
    core = import_module("autoskillit.core")

    assert set(authority.__all__) == _AUTHORITY_EXPORTS
    assert _AUTHORITY_EXPORTS <= set(legacy.__all__)

    for name in _AUTHORITY_EXPORTS:
        canonical = getattr(authority, name)
        assert getattr(legacy, name) is canonical
        assert getattr(core_types, name) is canonical
        assert getattr(core, name) is canonical
