"""Contract test for the #4391 Codex persistent_root scope-mismatch fix.

A recipe-level backend override pins a step to a persistent backend (codex)
while the *global* backend is non-persistent (claude-code). Before the fix,
DefaultSessionSkillManager resolved persistent_root once against the global
backend at make_context() time, so any step-level pin to a persistent
backend crashed with "A persistent_root is required for persistent
generated-home sessions". This test drives the real, unpatched manager
built by make_context() through run_skill() to prove the fix end to end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_recipe_pin_to_persistent_backend_resolves_root_when_global_backend_is_ephemeral(
    make_tool_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T5 — the #4391 contract test.

    Do not touch ctx.backend and do not replace ctx.session_skill_manager —
    the unpatched manager built by make_context() is the object under test.
    On develop before the #4391 fix, this test crashes with the persistent
    root RuntimeError; it is the bug reproduction.
    """
    from autoskillit.config import AgentBackendConfig, AutomationConfig
    from autoskillit.core import (
        CODEX_SESSIONS_SUBDIR,
        SkillExecutionRole,
        SkillSource,
        resolve_temp_dir,
    )
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server.tools.tools_execution import run_skill
    from autoskillit.workspace import EffectiveSkillInvocation, SkillInfo
    from tests.fakes import InMemoryHeadlessExecutor

    config = AutomationConfig(features={"fleet": True})
    config.agent_backend = AgentBackendConfig(
        backend="claude-code",
        recipe_overrides={"remediation": {"investigate": "codex"}},
    )
    ctx = make_tool_ctx(config)
    ctx.gate = DefaultGateState(enabled=True)
    ctx.kitchen_id = "test-kitchen"
    ctx.recipe_name = "remediation"

    executor = InMemoryHeadlessExecutor()
    ctx.executor = executor

    root = SkillInfo(
        name="investigate",
        source=SkillSource.BUNDLED_EXTENDED,
        path=tmp_path / "investigate" / "SKILL.md",
        canonical_content=(
            "---\nname: investigate\ndescription: Test skill.\n"
            "execution_role: session\n---\n# Investigate\n"
        ),
    )
    invocation = EffectiveSkillInvocation(
        root=root,
        closure=(root,),
        capability_union=frozenset(),
        project_root=tmp_path,
        execution_role=SkillExecutionRole.SESSION,
    )
    resolver = MagicMock()
    resolver.resolve_invocation.return_value = invocation
    ctx.skill_resolver = resolver

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda binary: f"/test-bin/{binary}",
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.is_feature_enabled",
        lambda *a, **kw: True,
    )

    # DefaultLaunchResolver.backend_for_authority proves environment-dependent
    # under the mock runner (it does real binary/path lookups); monkeypatch it
    # to return the real codex backend instance for this pinned step. Rooted
    # under tmp_path so it never touches the developer's real ~/.codex.
    real_codex = CodexBackend(source_codex_home=tmp_path / "codex-home")
    monkeypatch.setattr(
        ctx.launch_resolver,
        "backend_for_authority",
        lambda _authority: real_codex,
    )

    # session_skill_manager stays the real, unpatched object make_context()
    # built — the map it derives persistent_roots from is exactly what #4391
    # fixes. run_skill()'s finally block unconditionally calls
    # cleanup_session(), which removes the session's _session_roots entry and
    # generated home before control returns here. Wrap (not replace)
    # cleanup_session to capture that transient state at the moment cleanup
    # would destroy it, without altering any of the manager's real behavior —
    # the wrapped call still performs the real cleanup.
    manager = ctx.session_skill_manager
    assert manager is not None
    captured: dict[str, object] = {}
    original_cleanup_session = manager.cleanup_session

    def _spy_cleanup_session(session_id: str) -> bool:
        captured["session_roots"] = dict(manager._session_roots)
        session_root = manager._session_roots.get(session_id)
        if session_root is not None:
            captured["generated_home_existed"] = (session_root / session_id).exists()
        return original_cleanup_session(session_id)

    monkeypatch.setattr(manager, "cleanup_session", _spy_cleanup_session)

    response = json.loads(
        await run_skill(
            "/autoskillit:investigate backend-routing-test",
            str(tmp_path),
            step_name="investigate",
        )
    )

    # No RuntimeError escaped run_skill() — the #4391 crash reproduction.
    session_roots = cast("dict[str, Path]", captured.get("session_roots"))
    assert session_roots, f"expected materialization to populate _session_roots: {response}"
    assert len(session_roots) == 1, session_roots
    (actual_root,) = session_roots.values()
    expected_root = (
        resolve_temp_dir(tmp_path, config.workspace.temp_dir) / CODEX_SESSIONS_SUBDIR
    ).resolve()
    assert actual_root == expected_root
    assert captured.get("generated_home_existed") is True
