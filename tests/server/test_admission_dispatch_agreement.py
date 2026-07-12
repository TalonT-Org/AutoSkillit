"""Admission ↔ dispatch agreement test — the structural contract.

For every bundled recipe × backend combination, when admission says
``dispatch_feasible=True`` (and the recipe validates), every surviving
``run_skill`` step must pass the dispatch-time ``_is_backend_incompatible``
gate using the same per-step effective backend that ``run_skill`` computes.

This is the keystone test that makes the whole class of admission-vs-dispatch
disagreement bugs impossible to regress. Without it, admission could silently
admit pipelines that crash at ``run_skill`` time — the bug class fixed in
the rectify admission-truth-telling effort.

Requires ``DefaultSkillResolver`` to resolve real SKILL.md files from the
installed package. Filesystem access is required (no network).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY, get_backend
from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.io import builtin_recipes_dir
from autoskillit.recipe.io import load_recipe as load_recipe_yaml
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.server.tools._auto_overrides import (
    _backend_capability_overrides,
    _compute_effective_backend_map,
)
from autoskillit.server.tools.tools_execution import _is_backend_incompatible
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BUILTIN_DIR = builtin_recipes_dir()
_RECIPES: tuple[str, ...] = (
    "implementation",
    "implementation-groups",
    "remediation",
    "merge-prs",
)
_BACKENDS: tuple[str, ...] = tuple(sorted(BACKEND_REGISTRY.keys()))
_SKILL_RESOLVER = DefaultSkillResolver()


def _step_effective_backend(
    step: RecipeStep,
    step_name: str,
    backend_name: str,
    effective_map: dict[str, str] | None,
) -> str | None:
    """Mirror the per-step effective backend logic from tools_execution.py."""
    step_provider = getattr(step, "provider", "") or ""
    # Replicate the dispatch decision: ANTHROPIC_BASE_URL → claude-code override.
    # We don't have ProvidersConfig here; use the effective_backend_map if
    # provided (computed at IL-3 call sites with provider config).
    if effective_map and step_name in effective_map:
        return effective_map[step_name]
    # Fallback: if step has explicit provider at the recipe level, treat that
    # as a routing hint (the real code uses _resolve_provider_profile).
    if step_provider:
        # Without ProvidersConfig we cannot fully resolve provider_extras; for
        # this test we accept step_provider as a hint of claude-code routing
        # (the production schema only uses provider for that purpose).
        return "claude-code"
    return backend_name


def _dispatch_effective_backends(
    recipe: Recipe,
    backend_name: str,
    effective_map: dict[str, str] | None,
) -> dict[str, str]:
    """Compute dispatch-time effective backend for every run_skill step."""
    result: dict[str, str] = {}
    for step_name, step in recipe.steps.items():
        if getattr(step, "tool", None) != "run_skill":
            continue
        backend_result = _step_effective_backend(step, step_name, backend_name, effective_map)
        if backend_result is not None:
            result[step_name] = backend_result
    return result


@pytest.mark.parametrize("recipe_name", _RECIPES, ids=lambda x: x)
@pytest.mark.parametrize("backend_name", _BACKENDS, ids=lambda x: x)
def test_admission_dispatch_agreement(
    recipe_name: str, backend_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission agreement: when load_and_validate returns dispatch_feasible=True,
    every surviving run_skill step must pass _is_backend_incompatible against
    the dispatch-time effective backend."""
    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    backend = get_backend(backend_name)
    ingredient_overrides = _backend_capability_overrides(backend)

    # Pre-load recipe to compute effective backend map (mirrors IL-3 call sites).
    raw_recipe = load_recipe_yaml(_BUILTIN_DIR / f"{recipe_name}.yaml")
    effective_map = _compute_effective_backend_map(
        raw_recipe.steps,
        backend_name,
        None,
        recipe_name,
        skill_resolver=_SKILL_RESOLVER,
    )

    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        backend_name=backend_name,
        ingredient_overrides=ingredient_overrides,
        effective_backend_map=effective_map,
        lister=_SKILL_RESOLVER,
    )

    if not result.get("dispatch_feasible", True):
        # If admission refuses feasibility, the contract is trivially satisfied.
        return

    if not result.get("valid", False):
        # If admission produced errors, contract still holds (not feasible).
        return

    # Admission says feasible + valid → dispatch must agree for every step.
    dispatch_backends = _dispatch_effective_backends(raw_recipe, backend_name, effective_map)
    unresolvable: list[str] = []
    violations: list[str] = []

    for step_name, step in raw_recipe.steps.items():
        if getattr(step, "tool", None) != "run_skill":
            continue
        skill_cmd = getattr(step, "with_args", {}).get("skill_command", "")
        if "${" in skill_cmd or "<" in skill_cmd or "{" in skill_cmd:
            continue
        skill_name = skill_cmd.lstrip("/").split()[0] if skill_cmd.lstrip("/") else ""
        skill_name = skill_name.removeprefix("autoskillit:")
        if not skill_name:
            continue
        skill_info = _SKILL_RESOLVER.resolve(skill_name)
        if skill_info is None:
            unresolvable.append(
                f"{recipe_name}/{step_name}: skill '{skill_name}' is not resolvable "
                f"via DefaultSkillResolver — cannot verify dispatch compatibility"
            )
            continue
        step_effective = dispatch_backends.get(step_name)
        if step_effective is None:
            continue
        if _is_backend_incompatible(skill_info, step_effective):
            violations.append(
                f"{recipe_name}/{step_name}: admission says dispatch_feasible=True "
                f"but skill '{skill_name}' requires {sorted(skill_info.backend_requirements)} "
                f"and effective backend is '{step_effective}'"
            )

    assert not violations, "Admission ↔ dispatch agreement violated:\n  " + "\n  ".join(violations)
    assert not unresolvable, "Unresolvable skills (test infra gap):\n  " + "\n  ".join(
        unresolvable
    )


# ---------------------------------------------------------------------------
# Capability-route unit tests (audit remediation: Tests 3a–3d, REQ-ADMIT-002)
# ---------------------------------------------------------------------------


def _mock_git_write_resolver():
    """Resolver whose every skill carries git_metadata_write → claude-code."""
    from unittest.mock import MagicMock

    info = MagicMock()
    info.uses_capabilities = frozenset({"git_metadata_write"})
    info.backend_requirements = frozenset({"claude-code"})
    resolver = MagicMock()
    resolver.resolve.return_value = info
    return resolver


def _mock_run_skill_step():
    """Minimal run_skill step shape consumed by the admission helpers."""
    from unittest.mock import MagicMock

    step = MagicMock()
    step.tool = "run_skill"
    step.provider = ""
    step.with_args = {"skill_command": "/autoskillit:resolve-failures"}
    step.skip_when_false = "inputs.backend_supports_git_write"
    return step


def _mock_open_kitchen_resolver():
    """Resolver whose every skill carries open_kitchen (not-applicable, worker_routable=False)."""
    from unittest.mock import MagicMock

    info = MagicMock()
    info.uses_capabilities = frozenset({"open_kitchen"})
    info.backend_requirements = frozenset({"claude-code"})
    resolver = MagicMock()
    resolver.resolve.return_value = info
    return resolver


def test_open_kitchen_capability_not_routing_eligible_in_admission() -> None:
    """T-N2: _compute_effective_backend_map must not route open_kitchen-capability
    steps to claude-code — open_kitchen is not worker_routable."""
    from autoskillit.config._config_dataclasses import ProvidersConfig

    eff_map = _compute_effective_backend_map(
        {"step": _mock_run_skill_step()},
        "codex",
        ProvidersConfig(),
        "implementation",
        skill_resolver=_mock_open_kitchen_resolver(),
    )
    assert eff_map is not None
    assert eff_map.get("step") == "codex", (
        f"open_kitchen must not trigger capability routing: got {eff_map.get('step')!r}"
    )


def test_effective_backend_map_capability_route_unit() -> None:
    """Test 3a: _compute_effective_backend_map routes a git_metadata_write step
    to claude-code when a skill_resolver is supplied."""
    from autoskillit.config._config_dataclasses import ProvidersConfig

    eff_map = _compute_effective_backend_map(
        {"fix": _mock_run_skill_step()},
        "codex",
        ProvidersConfig(),
        "implementation",
        skill_resolver=_mock_git_write_resolver(),
    )
    assert eff_map is not None
    assert eff_map["fix"] == "claude-code"


def test_provider_aware_overrides_capability_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 3b: capability route with binary present flips the override to 'true'
    with resolution_path='capability_route'."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    overrides, detail = _provider_aware_capability_overrides(
        get_backend("codex"),
        "implementation",
        ProvidersConfig(),
        {"fix": _mock_run_skill_step()},
        skill_resolver=_mock_git_write_resolver(),
    )
    assert overrides["backend_supports_git_write"] == "true"
    assert detail.resolution_path == "capability_route"


def test_provider_aware_overrides_capability_route_no_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 3c: capability route with binary ABSENT fails closed —
    override stays 'false' with resolution_path='capability_route_no_binary'."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: None,
    )
    overrides, detail = _provider_aware_capability_overrides(
        get_backend("codex"),
        "implementation",
        ProvidersConfig(),
        {"fix": _mock_run_skill_step()},
        skill_resolver=_mock_git_write_resolver(),
    )
    assert overrides["backend_supports_git_write"] == "false"
    assert detail.resolution_path == "capability_route_no_binary"


def test_provider_aware_overrides_capability_route_none_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 3d (REQ-ADMIT-002): the capability branch functions when
    config_providers is None — zero provider config is the primary R0 scenario."""
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    overrides, detail = _provider_aware_capability_overrides(
        get_backend("codex"),
        "implementation",
        None,
        {"fix": _mock_run_skill_step()},
        skill_resolver=_mock_git_write_resolver(),
    )
    assert overrides["backend_supports_git_write"] == "true"
    assert detail.resolution_path == "capability_route"


def test_admission_dispatch_agreement_real_providers_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-TEST-006: the agreement contract exercised with a real (non-None)
    ProvidersConfig on codex+implementation — the combination the original
    incident chain shipped unchecked. Capability route + partial provider
    override must yield an admissible recipe with zero dispatch disagreements."""
    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    providers = ProvidersConfig(
        profiles={"minimax": {"ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1"}},
        step_overrides={"implement": "minimax"},
    )
    backend = get_backend("codex")
    raw_recipe = load_recipe_yaml(_BUILTIN_DIR / "implementation.yaml")

    overrides, _detail = _provider_aware_capability_overrides(
        backend,
        "implementation",
        providers,
        raw_recipe.steps,
        skill_resolver=_SKILL_RESOLVER,
    )
    assert overrides["backend_supports_git_write"] == "true"

    effective_map = _compute_effective_backend_map(
        raw_recipe.steps,
        "codex",
        providers,
        "implementation",
        skill_resolver=_SKILL_RESOLVER,
    )
    assert effective_map is not None

    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        backend_name="codex",
        ingredient_overrides=overrides,
        effective_backend_map=effective_map,
        lister=_SKILL_RESOLVER,
    )
    assert result.get("valid") is True
    assert result.get("dispatch_feasible") is True

    dispatch_backends = _dispatch_effective_backends(raw_recipe, "codex", effective_map)
    violations: list[str] = []
    for step_name, step in raw_recipe.steps.items():
        if getattr(step, "tool", None) != "run_skill":
            continue
        skill_cmd = getattr(step, "with_args", {}).get("skill_command", "")
        if "${" in skill_cmd or "<" in skill_cmd or "{" in skill_cmd:
            continue
        skill_name = skill_cmd.lstrip("/").split()[0] if skill_cmd.lstrip("/") else ""
        skill_name = skill_name.removeprefix("autoskillit:")
        if not skill_name:
            continue
        skill_info = _SKILL_RESOLVER.resolve(skill_name)
        if skill_info is None:
            continue
        step_effective = dispatch_backends.get(step_name)
        if step_effective is None:
            continue
        if _is_backend_incompatible(skill_info, step_effective):
            violations.append(f"{step_name}: '{skill_name}' vs '{step_effective}'")
    assert not violations, "Agreement violated under real ProvidersConfig:\n  " + "\n  ".join(
        violations
    )


# ---------------------------------------------------------------------------
# Real-registry routing tests (audit remediation: Tests 1.2, 1.4)
# ---------------------------------------------------------------------------


def _real_skill_resolver():
    """DefaultSkillResolver reading real SKILL.md files."""
    return DefaultSkillResolver()


def test_make_plan_reroutes_on_codex() -> None:
    """make-plan declares [agent_model, agent_subagent, cross_skill_ref] —
    all worker_routable post-fix — so it must reroute to claude-code on Codex."""
    from unittest.mock import MagicMock

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from autoskillit.server.tools.tools_execution import _has_routing_capability

    resolver = _real_skill_resolver()
    skill_info = resolver.resolve("make-plan")
    assert skill_info is not None, "make-plan must be resolvable from bundled SKILL.md"
    assert _has_routing_capability(skill_info.uses_capabilities) is True, (
        "make-plan declares agent_model, agent_subagent, cross_skill_ref — all worker_routable"
    )

    step = MagicMock()
    step.tool = "run_skill"
    step.provider = ""
    step.with_args = {"skill_command": "/autoskillit:make-plan"}
    step.skip_when_false = None

    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False

    eff_map = _compute_effective_backend_map(
        {"plan": step},
        "codex",
        ProvidersConfig(),
        "implementation",
        skill_resolver=resolver,
    )
    assert eff_map is not None
    assert eff_map.get("plan") == "claude-code", (
        f"make-plan step must route to claude-code, got {eff_map.get('plan')!r}"
    )


def test_investigate_reroutes_on_codex() -> None:
    """investigate declares [agent_model, claude_dir, cross_skill_ref] —
    agent_model and cross_skill_ref are worker_routable, so it must reroute."""
    from unittest.mock import MagicMock

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from autoskillit.server.tools.tools_execution import _has_routing_capability

    resolver = _real_skill_resolver()
    skill_info = resolver.resolve("investigate")
    assert skill_info is not None, "investigate must be resolvable from bundled SKILL.md"
    assert _has_routing_capability(skill_info.uses_capabilities) is True, (
        "investigate declares agent_model and cross_skill_ref — both worker_routable"
    )

    step = MagicMock()
    step.tool = "run_skill"
    step.provider = ""
    step.with_args = {"skill_command": "/autoskillit:investigate"}
    step.skip_when_false = None

    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False

    eff_map = _compute_effective_backend_map(
        {"inv": step},
        "codex",
        ProvidersConfig(),
        "implementation",
        skill_resolver=resolver,
    )
    assert eff_map is not None
    assert eff_map.get("inv") == "claude-code", (
        f"investigate step must route to claude-code, got {eff_map.get('inv')!r}"
    )


def test_prepare_issue_stays_on_codex() -> None:
    """prepare-issue declares only [github_api_write] (fix-required, worker_routable=False).
    Must NOT reroute on Codex — it runs on Codex with network_access=True injected."""
    from unittest.mock import MagicMock

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from autoskillit.server.tools.tools_execution import _has_routing_capability

    resolver = _real_skill_resolver()
    skill_info = resolver.resolve("prepare-issue")
    assert skill_info is not None
    assert _has_routing_capability(skill_info.uses_capabilities) is False, (
        "prepare-issue declares only github_api_write — not worker_routable"
    )

    step = MagicMock()
    step.tool = "run_skill"
    step.provider = ""
    step.with_args = {"skill_command": "/autoskillit:prepare-issue"}
    step.skip_when_false = None

    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False

    eff_map = _compute_effective_backend_map(
        {"prep": step},
        "codex",
        ProvidersConfig(),
        "implementation",
        skill_resolver=resolver,
    )
    assert eff_map is not None
    assert eff_map.get("prep") == "codex", (
        f"prepare-issue must NOT reroute on Codex, got {eff_map.get('prep')!r}"
    )


def test_codex_compatible_control_stays_on_codex() -> None:
    """open-kitchen declares only [open_kitchen] (not-applicable, worker_routable=False).
    It is a hard-block (not a reroute) — _has_routing_capability must return False."""
    from autoskillit.server.tools.tools_execution import _has_routing_capability

    resolver = _real_skill_resolver()
    skill_info = resolver.resolve("open-kitchen")
    assert skill_info is not None
    assert _has_routing_capability(skill_info.uses_capabilities) is False, (
        "open-kitchen declares only open_kitchen — not worker_routable"
    )


def test_auto_overrides_git_ingredient_not_set_by_agent_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent-only capabilities (agent_subagent) must NOT flip backend_supports_git_write.
    Only git_metadata_write maps to that ingredient per CAPABILITY_INGREDIENT_MAP."""
    from unittest.mock import MagicMock

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    info = MagicMock()
    info.uses_capabilities = frozenset({"agent_subagent"})
    info.backend_requirements = frozenset({"claude-code"})
    resolver = MagicMock()
    resolver.resolve.return_value = info

    step = MagicMock()
    step.tool = "run_skill"
    step.provider = ""
    step.with_args = {"skill_command": "/autoskillit:make-plan"}
    step.skip_when_false = None

    overrides, detail = _provider_aware_capability_overrides(
        get_backend("codex"),
        "implementation",
        ProvidersConfig(),
        {"plan": step},
        skill_resolver=resolver,
    )
    assert overrides["backend_supports_git_write"] == "false", (
        "agent_subagent must NOT flip backend_supports_git_write"
    )
    assert detail.resolution_path == "capability_route", (
        "Routing path still activated, but only mapped ingredients are set"
    )


def test_auto_overrides_git_ingredient_set_by_git_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git_metadata_write (worker_routable=True) MUST still flip backend_supports_git_write."""
    from unittest.mock import MagicMock

    from autoskillit.config._config_dataclasses import ProvidersConfig
    from autoskillit.server.tools._auto_overrides import _provider_aware_capability_overrides

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    info = MagicMock()
    info.uses_capabilities = frozenset({"git_metadata_write"})
    info.backend_requirements = frozenset({"claude-code"})
    resolver = MagicMock()
    resolver.resolve.return_value = info

    step = MagicMock()
    step.tool = "run_skill"
    step.provider = ""
    step.with_args = {"skill_command": "/autoskillit:resolve-failures"}
    step.skip_when_false = "inputs.backend_supports_git_write"

    overrides, detail = _provider_aware_capability_overrides(
        get_backend("codex"),
        "implementation",
        ProvidersConfig(),
        {"fix": step},
        skill_resolver=resolver,
    )
    assert overrides["backend_supports_git_write"] == "true", (
        "git_metadata_write must flip backend_supports_git_write"
    )
    assert detail.resolution_path == "capability_route"
