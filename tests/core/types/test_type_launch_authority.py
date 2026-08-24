"""Launch authority shard: validation contracts and facade wiring.

Exercises the defense boundaries the authority shard centralizes — the
post-init guards, from_payload decoders, payload round-trips, and the
package-facade publication of every public symbol.
"""

from __future__ import annotations

from importlib import import_module

import pytest

from autoskillit.core.types._type_execution_identity import BackendAuthorityKind
from autoskillit.core.types._type_launch_authority import (
    BackendAuthority,
    BackendAuthorityTier,
    LaunchFallbackRoute,
    LaunchSurface,
    LaunchValueSource,
    LaunchValueSourceKind,
    ModelPinResolution,
    ProviderBinding,
)
from autoskillit.core.types._type_launch_authority import __all__ as _authority_all
from autoskillit.core.types._type_launch_projection import LaunchContractError

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _source() -> LaunchValueSource:
    return LaunchValueSource(kind=LaunchValueSourceKind.RECIPE, key_path="recipe.skill")


def _authority() -> BackendAuthority:
    return BackendAuthority(
        backend="claude",
        kind=BackendAuthorityKind.RECIPE,
        tier=BackendAuthorityTier.RECIPE,
        key_path="recipe.skill.backend",
    )


class TestExportsAndFacades:
    def test_authority_shard_all_matches_imports(self) -> None:
        assert set(_authority_all) == {
            "BackendAuthority",
            "BackendAuthorityTier",
            "LaunchFallbackRoute",
            "LaunchSurface",
            "LaunchValueSource",
            "LaunchValueSourceKind",
            "ModelPinResolution",
            "ProviderBinding",
        }

    def test_package_facade_publishes_every_authority_symbol(self) -> None:
        core_types = import_module("autoskillit.core.types")
        core = import_module("autoskillit.core")
        for name in _authority_all:
            canonical = getattr(
                import_module("autoskillit.core.types._type_launch_authority"), name
            )
            assert getattr(core_types, name) is canonical
            assert getattr(core, name) is canonical


class TestBackendAuthority:
    def test_frozen_blocks_mutation(self) -> None:
        authority = _authority()
        with pytest.raises(AttributeError):
            authority.backend = "codex"  # type: ignore[misc]

    def test_post_init_rejects_blank_backend(self) -> None:
        with pytest.raises(LaunchContractError, match="backend"):
            BackendAuthority(
                backend="   ",
                kind=BackendAuthorityKind.RECIPE,
                tier=BackendAuthorityTier.RECIPE,
                key_path="recipe.skill.backend",
            )

    def test_post_init_rejects_blank_key_path(self) -> None:
        with pytest.raises(LaunchContractError, match="key path"):
            BackendAuthority(
                backend="claude",
                kind=BackendAuthorityKind.RECIPE,
                tier=BackendAuthorityTier.RECIPE,
                key_path="",
            )

    def test_post_init_rejects_kind_tier_mismatch(self) -> None:
        with pytest.raises(LaunchContractError, match="requires tier"):
            BackendAuthority(
                backend="claude",
                kind=BackendAuthorityKind.RECIPE,
                tier=BackendAuthorityTier.GLOBAL,
                key_path="recipe.skill.backend",
            )

    def test_to_payload_round_trips_through_from_payload(self) -> None:
        payload = _authority().to_payload()
        rebuilt = BackendAuthority.from_payload(payload)
        assert rebuilt == _authority()

    def test_from_payload_rejects_extra_field(self) -> None:
        payload = {**_authority().to_payload(), "unexpected": "x"}
        with pytest.raises(LaunchContractError, match="not canonical"):
            BackendAuthority.from_payload(payload)

    def test_from_payload_rejects_missing_field(self) -> None:
        payload = dict(_authority().to_payload())
        del payload["key_path"]
        with pytest.raises(LaunchContractError, match="not canonical"):
            BackendAuthority.from_payload(payload)

    def test_from_payload_rejects_non_string_fields(self) -> None:
        payload = {**_authority().to_payload(), "backend": 42}
        with pytest.raises(LaunchContractError, match="must be strings"):
            BackendAuthority.from_payload(payload)

    def test_from_payload_names_offending_kind_value(self) -> None:
        payload = {**_authority().to_payload(), "kind": "not-a-kind"}
        with pytest.raises(LaunchContractError, match=r"field 'kind' value 'not-a-kind'"):
            BackendAuthority.from_payload(payload)

    def test_from_payload_names_offending_tier_value(self) -> None:
        payload = {**_authority().to_payload(), "tier": "primary"}
        with pytest.raises(LaunchContractError, match=r"field 'tier' value 'primary'"):
            BackendAuthority.from_payload(payload)


class TestLaunchValueSource:
    def test_post_init_rejects_blank_key_path(self) -> None:
        with pytest.raises(LaunchContractError, match="key path"):
            LaunchValueSource(kind=LaunchValueSourceKind.RECIPE, key_path="")

    def test_to_payload_round_trips(self) -> None:
        src = _source()
        rebuilt = LaunchValueSource(
            kind=LaunchValueSourceKind(src.to_payload()["kind"]),
            key_path=src.to_payload()["key_path"],
        )
        assert rebuilt == src


class TestProviderBinding:
    def _binding(self, **overrides: object) -> ProviderBinding:
        defaults: dict[str, object] = {
            "provider": "anthropic",
            "profile": "work",
            "required_backend": "claude",
            "normalized_endpoint": "https://api.anthropic.com",
            "key_path": "recipe.skill.provider",
            "provider_source": _source(),
            "profile_source": _source(),
            "endpoint_source": _source(),
        }
        defaults.update(overrides)
        return ProviderBinding(**defaults)  # type: ignore[arg-type]

    def test_post_init_rejects_blank_provider(self) -> None:
        with pytest.raises(LaunchContractError, match="provider"):
            self._binding(provider="")

    def test_post_init_rejects_blank_profile(self) -> None:
        with pytest.raises(LaunchContractError, match="profile"):
            self._binding(profile="")

    def test_post_init_rejects_blank_required_backend(self) -> None:
        with pytest.raises(LaunchContractError, match="required_backend"):
            self._binding(required_backend="")

    def test_post_init_rejects_blank_key_path(self) -> None:
        with pytest.raises(LaunchContractError, match="key_path"):
            self._binding(key_path="   ")

    def test_post_init_rejects_empty_secret_environment_key(self) -> None:
        with pytest.raises(LaunchContractError, match="non-empty"):
            self._binding(secret_environment_keys=("VALID", ""))

    def test_post_init_rejects_secret_overlap_with_environment(self) -> None:
        with pytest.raises(LaunchContractError, match="nonsecret"):
            self._binding(
                environment={"SHARED": "plain"},
                secret_environment_keys=("SHARED",),
            )

    def test_post_init_sorts_and_dedupes_secret_environment_keys(self) -> None:
        binding = self._binding(secret_environment_keys=("B", "A", "B"))
        assert binding.secret_environment_keys == ("A", "B")

    def test_post_init_freezes_environment_mapping(self) -> None:
        binding = self._binding(environment={"KEY": "value"})
        with pytest.raises(TypeError):
            binding.environment["KEY"] = "tampered"  # type: ignore[index]

    def test_frozen_blocks_mutation(self) -> None:
        binding = self._binding()
        with pytest.raises(AttributeError):
            binding.provider = "openai"  # type: ignore[misc]


class TestLaunchFallbackRoute:
    def _route(self, **overrides: object) -> LaunchFallbackRoute:
        defaults: dict[str, object] = {
            "backend": "codex",
            "provider": "openai",
            "profile": "explore",
            "model": "gpt-5",
            "source": _source(),
        }
        defaults.update(overrides)
        return LaunchFallbackRoute(**defaults)  # type: ignore[arg-type]

    def test_post_init_rejects_blank_backend(self) -> None:
        with pytest.raises(LaunchContractError, match="backend"):
            self._route(backend="")

    def test_post_init_rejects_blank_provider(self) -> None:
        with pytest.raises(LaunchContractError, match="provider"):
            self._route(provider="   ")

    def test_post_init_rejects_blank_profile(self) -> None:
        with pytest.raises(LaunchContractError, match="profile"):
            self._route(profile="")

    def test_post_init_rejects_blank_model(self) -> None:
        with pytest.raises(LaunchContractError, match="model"):
            self._route(model="")

    def test_to_payload_round_trips(self) -> None:
        route = self._route()
        payload = route.to_payload()
        assert payload["backend"] == "codex"
        assert payload["provider"] == "openai"
        assert payload["profile"] == "explore"
        assert payload["model"] == "gpt-5"
        assert payload["source"] == dict(route.source.to_payload())

    def test_frozen_blocks_mutation(self) -> None:
        route = self._route()
        with pytest.raises(AttributeError):
            route.model = "gpt-6"  # type: ignore[misc]


class TestModelPinResolution:
    def test_holds_model_and_source(self) -> None:
        resolution = ModelPinResolution(model="gpt-5", source=_source())
        assert resolution.model == "gpt-5"
        assert resolution.source is not None

    def test_frozen_blocks_mutation(self) -> None:
        resolution = ModelPinResolution(model="gpt-5", source=_source())
        with pytest.raises(AttributeError):
            resolution.model = "gpt-6"  # type: ignore[misc]


class TestLaunchSurfaceEnum:
    def test_includes_all_expected_surfaces(self) -> None:
        assert {surface.value for surface in LaunchSurface} == {
            "headless-skill",
            "fleet-outer",
            "fleet-step-non-resumable",
            "interactive-cook",
            "interactive-order",
        }
