"""LLM provider / agent backend dataclasses plus the retired-profile-key registry.

Owns: ``CoreRunConfig`` (the ``model`` section), ``ProviderProfileDef`` (the
frozen/slots registry entry for a named profile), ``ExecutionCandidateSpec``
(an ordered execution fallback entry), ``ProvidersConfig`` (the ``providers``
section with ``resolved_profiles`` coercion), and
``AgentBackendConfig`` (the ``agent_backend`` section).

Also owns the ``RETIRED_PROFILE_KEYS`` registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from autoskillit.core import KNOWN_BACKEND_NAMES, get_logger

logger = get_logger(__name__)

# Retired profile YAML keys. Append-only; entries require a trailing comment
# naming the retiring version and tracking issue.
RETIRED_PROFILE_KEYS: frozenset[str] = frozenset(
    {
        # Removed in 0.10.1007. No consumer existed: _profile_to_env projects
        # base_url / timeout_seconds / api_key_env / raw_env only. See #4685.
        "context_window",
    }
)


def _normalize_recipe_overrides(
    recipe_overrides: Mapping[str, dict[str, str] | None],
) -> dict[str, dict[str, str]]:
    return {
        recipe: (overrides if overrides is not None else {})
        for recipe, overrides in recipe_overrides.items()
    }


@dataclass
class CoreRunConfig:
    default_model: str = "sonnet"
    model_override: str | None = None
    provider: str = "anthropic"
    step_overrides: dict[str, str] = field(default_factory=dict)
    recipe_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.default_model:
            raise ValueError("CoreRunConfig.default_model must not be empty")
        self.recipe_overrides = _normalize_recipe_overrides(self.recipe_overrides)
        for step, model_val in self.step_overrides.items():
            if not isinstance(model_val, str):
                raise ValueError(
                    f"step_overrides[{step!r}] must be a string, got {type(model_val).__name__!r}"
                )
        for recipe, overrides in self.recipe_overrides.items():
            if not isinstance(overrides, dict):
                raise ValueError(
                    f"recipe_overrides[{recipe!r}] must be a dict, "
                    f"got {type(overrides).__name__!r}"
                )
            for step, model_val in overrides.items():
                if not isinstance(model_val, str):
                    raise ValueError(
                        f"recipe_overrides[{recipe!r}][{step!r}] must be a string, "
                        f"got {type(model_val).__name__!r}"
                    )


@dataclass(frozen=True, slots=True)
class ProviderProfileDef:
    """Static definition of a named LLM provider profile.

    Used as an element in a provider registry. Immutable after construction.
    """

    name: str
    base_url: str | None = None
    timeout_seconds: int | None = None
    api_key_env: str | None = None
    raw_env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds < 0:
            raise ValueError(f"timeout_seconds must be non-negative, got {self.timeout_seconds}")


@dataclass(frozen=True, slots=True)
class ExecutionCandidateSpec:
    """One ordered backend/provider/model candidate for skill execution."""

    backend: str
    profile: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str) or self.backend not in KNOWN_BACKEND_NAMES:
            raise ValueError(
                f"execution candidate backend must be one of "
                f"{sorted(KNOWN_BACKEND_NAMES)!r}, got {self.backend!r}"
            )
        for field_name, value in (("profile", self.profile), ("model", self.model)):
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"execution candidate {field_name} must be a string or null, "
                    f"got {type(value).__name__!r}"
                )


@dataclass
class ProvidersConfig:
    """Configuration for alternative LLM provider routing.

    API keys must live in .secrets.yaml or environment variables and must
    never be committed to version-controlled config files.
    """

    default_provider: str | None = None
    profiles: dict[str, dict[str, str | None]] = field(default_factory=dict)
    step_overrides: dict[str, str] = field(default_factory=dict)
    recipe_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    model_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    execution_candidates: list[ExecutionCandidateSpec] = field(default_factory=list)
    provider_retry_limit: int = 2

    def __post_init__(self) -> None:
        if self.provider_retry_limit < 1:
            raise ValueError(f"provider_retry_limit must be >= 1, got {self.provider_retry_limit}")
        self._validate_execution_candidates()
        self._validate_profiles()
        self.recipe_overrides = _normalize_recipe_overrides(self.recipe_overrides)
        self._validate_recipe_overrides()
        self._validate_model_overrides()
        self._warn_unknown_profiles()

    def _validate_execution_candidates(self) -> None:
        if not isinstance(self.execution_candidates, list):
            raise ValueError("execution_candidates must be a list")
        for index, candidate in enumerate(self.execution_candidates):
            if not isinstance(candidate, ExecutionCandidateSpec):
                raise ValueError(
                    f"execution_candidates[{index}] must be an ExecutionCandidateSpec, "
                    f"got {type(candidate).__name__!r}"
                )

    def _validate_profiles(self) -> None:
        for name, profile in self.profiles.items():
            for k, v in profile.items():
                if v is not None and not isinstance(v, str):
                    raise ValueError(
                        f"profiles[{name!r}][{k!r}] must be a string or null, "
                        f"got {type(v).__name__!r}"
                    )

    def _validate_recipe_overrides(self) -> None:
        for recipe, overrides in self.recipe_overrides.items():
            if not isinstance(overrides, dict):
                raise ValueError(
                    f"recipe_overrides[{recipe!r}] must be a dict, "
                    f"got {type(overrides).__name__!r}"
                )
            for step, provider in overrides.items():
                if not isinstance(provider, str):
                    raise ValueError(
                        f"recipe_overrides[{recipe!r}][{step!r}] must be a string, "
                        f"got {type(provider).__name__!r}"
                    )

    def _validate_model_overrides(self) -> None:
        for recipe, overrides in self.model_overrides.items():
            if not isinstance(overrides, dict):
                raise ValueError(
                    f"model_overrides[{recipe!r}] must be a dict, got {type(overrides).__name__!r}"
                )
            for step, model_val in overrides.items():
                if not isinstance(model_val, str):
                    raise ValueError(
                        f"model_overrides[{recipe!r}][{step!r}] must be a string, "
                        f"got {type(model_val).__name__!r}"
                    )

    def _warn_unknown_profiles(self) -> None:
        known = set(self.profiles.keys()) | {"anthropic"}
        for step, profile_name in self.step_overrides.items():
            if profile_name not in known:
                logger.warning(
                    "step_override_references_unknown_profile",
                    step=step,
                    profile=profile_name,
                    known_profiles=sorted(known),
                )
        for recipe, step_map in self.recipe_overrides.items():
            for step, profile_name in step_map.items():
                if profile_name not in known:
                    logger.warning(
                        "recipe_override_references_unknown_profile",
                        recipe=recipe,
                        step=step,
                        profile=profile_name,
                        known_profiles=sorted(known),
                    )

    @property
    def resolved_profiles(self) -> dict[str, ProviderProfileDef]:
        result: dict[str, ProviderProfileDef] = {}
        for name, raw_dict in self.profiles.items():
            copy = {k: v for k, v in raw_dict.items() if v is not None}
            base_url = copy.pop("base_url", None)
            timeout_str = copy.pop("timeout_seconds", None)
            api_key_env = copy.pop("api_key_env", None)
            # Drop retired keys before raw_env captures the remaining provider fields.
            for retired_key in RETIRED_PROFILE_KEYS:
                copy.pop(retired_key, None)
            result[name] = ProviderProfileDef(
                name=name,
                base_url=base_url,
                timeout_seconds=int(timeout_str)
                if timeout_str is not None and timeout_str != ""
                else None,
                api_key_env=api_key_env,
                raw_env=copy,
            )
        return result


@dataclass
class AgentBackendConfig:
    backend: str = "claude-code"
    step_overrides: dict[str, str] = field(default_factory=dict)
    recipe_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    # Repository-scoped toggle: when True, the Claude launcher neutralizes
    # CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS in both the process env and the
    # target repository's .claude/settings*.json files before spawn. Defaults
    # to False — repositories with the option disabled remain byte-for-byte
    # unchanged. Independent from join.required. Refs #4575.
    force_inactive_agent_teams: bool = False
    # When True, open_kitchen (both visibility branches) and _pre_reveal_kitchen
    # pre-apply the "exploration" tag reveal alongside kitchen/plan-review, for
    # session types eligible to bind exploration authority. Defaults to False —
    # the HMAC capability lease remains the authorization boundary regardless;
    # this only auto-provisions the weaker visibility gate. consumer:
    # server/tools/tools_kitchen/_open_kitchen/_orchestrator.py open_kitchen,
    # server/lifecycle/_lifespan/_session_boots.py _pre_reveal_kitchen. Refs #4684.
    auto_provision_exploration: bool = False

    def __post_init__(self) -> None:
        self._validate_backend()
        self._validate_step_overrides()
        self.recipe_overrides = _normalize_recipe_overrides(self.recipe_overrides)
        self._validate_recipe_overrides()

    def _validate_backend(self) -> None:
        if not self.backend:
            raise ValueError("backend must not be empty")
        elif self.backend not in KNOWN_BACKEND_NAMES:
            logger.warning(
                "unknown_backend",
                backend=self.backend,
                valid_names=sorted(KNOWN_BACKEND_NAMES),
            )

    def _validate_step_overrides(self) -> None:
        for step_name, override_backend in self.step_overrides.items():
            if not isinstance(step_name, str):
                raise ValueError(
                    f"agent_backend.step_overrides keys must be strings; "
                    f"got {type(step_name).__name__}"
                )
            if not isinstance(override_backend, str):
                raise ValueError(
                    f"agent_backend.step_overrides[{step_name!r}] must be a string; "
                    f"got {type(override_backend).__name__}"
                )
            if override_backend not in KNOWN_BACKEND_NAMES:
                logger.warning(
                    "step_override_references_unknown_backend",
                    step=step_name,
                    backend=override_backend,
                    valid_names=sorted(KNOWN_BACKEND_NAMES),
                )

    def _validate_recipe_overrides(self) -> None:
        for recipe_name, recipe_map in self.recipe_overrides.items():
            if not isinstance(recipe_name, str):
                raise ValueError(
                    f"agent_backend.recipe_overrides keys must be strings; "
                    f"got {type(recipe_name).__name__}"
                )
            if not isinstance(recipe_map, dict):
                raise ValueError(
                    f"agent_backend.recipe_overrides[{recipe_name!r}] must be a dict; "
                    f"got {type(recipe_map).__name__}"
                )
            for step_name, override_backend in recipe_map.items():
                if not isinstance(step_name, str):
                    raise ValueError(
                        f"agent_backend.recipe_overrides[{recipe_name!r}] keys must be "
                        f"strings; got {type(step_name).__name__}"
                    )
                if not isinstance(override_backend, str):
                    raise ValueError(
                        f"agent_backend.recipe_overrides[{recipe_name!r}][{step_name!r}] "
                        f"must be a string; got {type(override_backend).__name__}"
                    )
                if override_backend not in KNOWN_BACKEND_NAMES:
                    logger.warning(
                        "recipe_override_references_unknown_backend",
                        recipe=recipe_name,
                        step=step_name,
                        backend=override_backend,
                        valid_names=sorted(KNOWN_BACKEND_NAMES),
                    )
