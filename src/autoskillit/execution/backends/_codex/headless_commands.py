"""Codex ordinary-headless command construction.

Split out of ``codex.py`` along the ``CodexSessionCommandMixin`` boundary to
stay under the file-length hard cap; owns the ``build_headless_cmd`` builder.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from autoskillit.core import (
    AUTOSKILLIT_INSTALLED_VERSION,
    CODEX_HOME_ENV_VAR,
    CODEX_RESERVED_HOME_ENV_VARS,
    CmdSpec,
    CodexAppServerPlan,
)
from autoskillit.execution.backends._backend_cmd_builder_base import _merge_caller_env_extras
from autoskillit.execution.backends._claude_prompt import _HEADLESS_EXCLUSIVE_VARS
from autoskillit.execution.backends._codex.session_commands import CodexSessionCommandMixin
from autoskillit.execution.backends._codex_cmd_builders import (
    _codex_app_server_base,
    _codex_exec_extras,
    _should_bypass_hook_trust,
)


class CodexHeadlessCommandMixin(CodexSessionCommandMixin):
    """Ordinary (no managed catalog) Codex headless command construction."""

    def build_headless_cmd(
        self,
        prompt: str,
        *,
        model: str | None = None,
        add_dirs: Sequence[str] = (),
        force_inactive_agent_teams: bool = False,  # no-op: Codex has no team concept
        env_extras: Mapping[str, str] | None = None,
        project_root: Path | str | None = None,
    ) -> CmdSpec:
        """Ordinary headless launch: no managed catalog, app-server transport.

        Registers no extra skill root and skips catalog attestation — the
        server's native (or, when the finalized environment already carries
        one, an explicit) home is used as-is. ``add_dirs`` becomes
        ``runtime_workspace_roots`` on the driver plan rather than
        ``--add-dir`` argv; every entry must already be absolute (enforced
        by ``CodexAppServerPlan.__post_init__``).
        """
        headless_extras = _codex_exec_extras(session_type="")
        _merge_caller_env_extras(headless_extras, env_extras)
        # CODEX_HOME is one of _HEADLESS_EXCLUSIVE_VARS (stripped below from the
        # subprocess's own base env to block host leakage) and one of the
        # reserved keys _merge_caller_env_extras always blocks from caller
        # extras — so the only way an explicit ambient home reaches the child
        # at all is to read it directly off this process's environment here
        # and re-inject it ourselves, exactly like every other builder injects
        # a bound managed session_home into its own reserved keys.
        session_home = os.environ.get(CODEX_HOME_ENV_VAR, "")
        if session_home:
            for reserved_key in CODEX_RESERVED_HOME_ENV_VARS:
                headless_extras[reserved_key] = session_home
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = self.env_policy().build_env(filtered_base, extras=headless_extras)
        cmd = _codex_app_server_base(extra_overrides=self._otlp_overrides(headless_extras))
        bypass_hook_trust = _should_bypass_hook_trust(
            self.capabilities.hook_trust_policy, automated_session=True
        )
        config_overrides: dict[str, object] = {"bypass_hook_trust": bypass_hook_trust}
        if model:
            for override in self.model_config_overrides(model):
                key, _, value = override.partition("=")
                config_overrides[key] = value
        cwd = str(project_root) if project_root is not None else ""
        app_server_plan = CodexAppServerPlan(
            session_home=session_home,
            catalog_root="",
            expected_skill_names=frozenset(),
            expected_skill_entries=(),
            cwd=cwd,
            prompt=prompt,
            model=self.translate_model(model) if model else None,
            sandbox="workspace-write",
            approval_policy="never",
            bypass_hook_trust=bypass_hook_trust,
            developer_instructions=None,
            config_overrides=config_overrides,
            client_version=AUTOSKILLIT_INSTALLED_VERSION,
            runtime_workspace_roots=tuple(add_dirs),
        )
        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            cwd=cwd,
            app_server_plan=app_server_plan,
            force_inactive_agent_teams=force_inactive_agent_teams,
        )


__all__ = ["CodexHeadlessCommandMixin"]
