"""LLM provider / agent backend dataclasses plus the retired-profile-key registry.

Owns: ``CoreRunConfig`` (the ``model`` section), ``ProviderProfileDef`` (the
frozen/slots registry entry for a named profile), ``ProvidersConfig`` (the
``providers`` section with ``resolved_profiles`` coercion), and
``AgentBackendConfig`` (the ``agent_backend`` section).

Also owns the ``RETIRED_PROFILE_KEYS`` registry plus the module-load invariant
``_NON_LOWER_RETIRED_PROFILE_KEYS``. The invariant lives next to the registry so
it fires whenever any caller imports ``RETIRED_PROFILE_KEYS`` — not only when
they happen to import the retired-keys section config module.
"""

from __future__ import annotations

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

# Fail fast at module load; an explicit raise keeps the check active under `python -O`.
_NON_LOWER_RETIRED_PROFILE_KEYS = sorted(
    k for k in RETIRED_PROFILE_KEYS if not isinstance(k, str) or k != k.lower()
)
if _NON_LOWER_RETIRED_PROFILE_KEYS:
    raise AssertionError(
        f"RETIRED_PROFILE_KEYS entries must be lowercase str; offenders: "
        f"{_NON_LOWER_RETIRED_PROFILE_KEYS!r}"
    )
del _NON_LOWER_RETIRED_PROFILE_KEYS


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
        # Coerce None recipe_overrides entries to empty dicts. YAML sections with
        # all children commented out (or otherwise empty) parse to None; treating
        # them as empty matches user intent and avoids spurious validation errors
        # during collection when a user-level ~/.autoskillit/config.yaml contains
        # such a section.
        self.recipe_overrides = {
            recipe: (overrides if overrides is not None else {})
            for recipe, overrides in self.recipe_overrides.items()
        }
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
    provider_retry_limit: int = 2

    def __post_init__(self) -> None:
        if self.provider_retry_limit < 1:
            raise ValueError(f"provider_retry_limit must be >= 1, got {self.provider_retry_limit}")
        for name, profile in self.profiles.items():
            for k, v in profile.items():
                if v is not None and not isinstance(v, str):
                    raise ValueError(
                        f"profiles[{name!r}][{k!r}] must be a string or null, "
                        f"got {type(v).__name__!r}"
                    )
        # Coerce None recipe_overrides entries to empty dicts. YAML sections with
        # all children commented out (or otherwise empty) parse to None; treating
        # them as empty matches user intent and avoids spurious validation errors
        # during collection when a user-level ~/.autoskillit/config.yaml contains
        # such a section.
        self.recipe_overrides = {
            recipe: (overrides if overrides is not None else {})
            for recipe, overrides in self.recipe_overrides.items()
        }
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
    # server/tools/tools_kitchen/_open_kitchen.py open_kitchen,
    # server/_lifespan/_session_boots.py _pre_reveal_kitchen. Refs #4684.
    auto_provision_exploration: bool = False

    def __post_init__(self) -> None:
        if not self.backend:
            raise ValueError("backend must not be empty")
        elif self.backend not in KNOWN_BACKEND_NAMES:
            logger.warning(
                "unknown_backend",
                backend=self.backend,
                valid_names=sorted(KNOWN_BACKEND_NAMES),
            )

        # Validate step_overrides shape: values must be strings.
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

        # Coerce None recipe_overrides entries to empty dicts. YAML sections with
        # all children commented out (or otherwise empty) parse to None; treating
        # them as empty matches user intent and avoids spurious validation errors
        # during collection when a user-level ~/.autoskillit/config.yaml contains
        # such a section.
        self.recipe_overrides = {
            recipe: (overrides if overrides is not None else {})
            for recipe, overrides in self.recipe_overrides.items()
        }

        # Validate recipe_overrides shape: outer values are dicts, inner values are strings.
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
