"""T5/T6: explorer role TOML generation derives from validated bindings."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    EXPLORATION_TOOLS,
    load_bundled_agent_definitions,
)
from autoskillit.execution.backends.codex import _generate_agent_tomls
from tests._codex_feature_policy import RETIRED_CODEX_FEATURES

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_FORBIDDEN_CODEX_FEATURES = (*RETIRED_CODEX_FEATURES, "web_search")


class TestExplorerRegistrationDerivesFromBindings:
    """T5: setup_session_dir with no bindings produces no explorer-role TOMLs."""

    def test_unbound_excludes_explorer_roles(self, tmp_path: Path) -> None:
        count = _generate_agent_tomls(tmp_path)
        toml_names = {p.stem for p in (tmp_path / "agents").glob("*.toml")}
        assert not (toml_names & BUNDLED_EXPLORER_ROLES), (
            f"unbound generation must not produce explorer role TOMLs, "
            f"but found: {toml_names & BUNDLED_EXPLORER_ROLES}"
        )
        all_defs = load_bundled_agent_definitions()
        definitions = {definition.name: definition for definition in all_defs}
        non_explorer_count = sum(1 for d in all_defs if d.name not in BUNDLED_EXPLORER_ROLES)
        assert count == non_explorer_count
        for path in (tmp_path / "agents").glob("*.toml"):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            expected = definitions[path.stem].codex.web_search
            assert data.get("web_search") == expected

    @pytest.mark.parametrize(
        "config_text",
        [None, "[mcp_servers.autoskillit]\n"],
    )
    def test_direct_roles_fail_closed_without_canonical_transport(
        self,
        tmp_path: Path,
        config_text: str | None,
    ) -> None:
        definitions = tuple(
            definition
            for definition in load_bundled_agent_definitions()
            if definition.name in BUNDLED_EXPLORER_ROLES
        )
        if config_text is not None:
            (tmp_path / "config.toml").write_text(config_text)

        with pytest.raises(ValueError, match="canonical"):
            _generate_agent_tomls(tmp_path, agent_defs=definitions)

    def test_bound_includes_explorer_roles(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex.explorer_projection import (
            _EXPLORER_BINDING_ENV_KEYS,
        )

        binding = {key: f"test-value-{key}" for key in _EXPLORER_BINDING_ENV_KEYS}
        binding["AUTOSKILLIT_EXPLORATION_ROLE"] = "shared-explorer-session"
        binding["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"] = "/tmp/test-authority"
        bindings = {role: dict(binding) for role in sorted(BUNDLED_EXPLORER_ROLES)}
        transport = {"command": "/usr/bin/fake-mcp"}

        count = _generate_agent_tomls(
            tmp_path,
            explorer_binding_envs=bindings,
            explorer_mcp_transport=transport,
        )
        toml_names = {p.stem for p in (tmp_path / "agents").glob("*.toml")}
        assert BUNDLED_EXPLORER_ROLES <= toml_names, (
            f"bound generation must include explorer role TOMLs, "
            f"missing: {BUNDLED_EXPLORER_ROLES - toml_names}"
        )
        all_defs = load_bundled_agent_definitions()
        assert count == len(all_defs)

    def test_bound_explorer_toml_has_correct_sandbox(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex.explorer_projection import (
            _EXPLORER_BINDING_ENV_KEYS,
        )

        binding = {key: f"test-value-{key}" for key in _EXPLORER_BINDING_ENV_KEYS}
        binding["AUTOSKILLIT_EXPLORATION_ROLE"] = "shared-explorer-session"
        binding["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"] = "/tmp/test-authority"
        bindings = {role: dict(binding) for role in sorted(BUNDLED_EXPLORER_ROLES)}
        transport = {"command": "/usr/bin/fake-mcp"}

        _generate_agent_tomls(
            tmp_path,
            explorer_binding_envs=bindings,
            explorer_mcp_transport=transport,
        )
        for role in BUNDLED_EXPLORER_ROLES:
            data = tomllib.loads(
                (tmp_path / "agents" / f"{role}.toml").read_text(encoding="utf-8")
            )
            assert data["sandbox_mode"] == "read-only", (
                f"explorer role {role} must have sandbox_mode=read-only"
            )
            assert data["web_search"] == "disabled"
            assert not (set(_FORBIDDEN_CODEX_FEATURES) & set(data.get("features", {})))
            assert frozenset(data["mcp_servers"]["autoskillit"]["enabled_tools"]) == frozenset(
                EXPLORATION_TOOLS
            )
