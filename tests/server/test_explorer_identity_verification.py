"""Decode-once native explorer identity verification contracts."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    ExplorationApplicability,
    ExplorationRouterPlan,
    ExplorationVectorDisposition,
    ProfileActivation,
    RepositoryProfileId,
    SkillExecutionRole,
    SkillSource,
    pkg_root,
    validate_add_dir,
)
from autoskillit.execution.backends._explorer_dispatch import (
    CLAUDE_EXPLORATION_DISPATCH_RENDERER,
    CODEX_EXPLORATION_DISPATCH_RENDERER,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.server._explorer_projection import (
    _build_requested_execution_identity,
    _has_exact_identity_field,
)
from autoskillit.workspace import EffectiveSkillInvocation
from autoskillit.workspace._projected_artifact.materialization import (
    SkillProjectionContext,
    project_agent_skill_document,
)
from autoskillit.workspace.skills import (
    _bind_exploration_vector_markers,
    _load_exploration_sidecar,
    _parse_exploration_sidecar,
    _skill_info_from_frontmatter,
)

pytestmark = [
    pytest.mark.layer("server"),
    pytest.mark.feature("exploration"),
    pytest.mark.small,
]

# A real always-active, multi-vector migrated skill — used both to exercise a
# genuine backend-native rendered packet and as the dd30c7e6e regression fixture.
_ALWAYS_ACTIVE_SKILL = "arch-lens-security"


def _first_migrated_security_vector():
    path = pkg_root() / "skills_extended" / _ALWAYS_ACTIVE_SKILL / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    sidecar_data, _digest = _load_exploration_sidecar(path)
    vectors = _parse_exploration_sidecar(sidecar_data, _ALWAYS_ACTIVE_SKILL)
    bound = _bind_exploration_vector_markers(content, vectors)
    first = bound[0]
    assert first.disposition is ExplorationVectorDisposition.MIGRATED
    return replace(
        first,
        profile=RepositoryProfileId.AUTOSKILLIT,
        task=replace(first.task, profile=RepositoryProfileId.AUTOSKILLIT),
    )


def _single_vector_plan(vector) -> ExplorationRouterPlan:
    return ExplorationRouterPlan(
        snapshot=None,
        tasks=(vector.task,),
        activations=(
            ProfileActivation(
                vector.profile,
                ExplorationApplicability.APPLICABLE,
                "trusted identity-verification test profile",
            ),
        ),
    )


def _decode_message_argument(rendered_call: str, message_argument: str) -> str:
    """Extract and JSON-decode a rendered call's message/prompt argument.

    Mirrors the decode-once extraction in ``_build_requested_execution_identity``:
    the renderer embeds the prompt via ``json.dumps()``, so a projected packet's
    identity fields live inside an escaped JSON string literal, not as raw lines.
    """
    pattern = re.compile(rf'{re.escape(message_argument)}=("(?:[^"\\]|\\.)*")')
    match = pattern.search(rendered_call)
    assert match is not None
    decoded = json.loads(match.group(1))
    assert isinstance(decoded, str)
    return decoded


def test_identity_field_recognized_in_claude_rendered_packet() -> None:
    vector = _first_migrated_security_vector()
    plan = _single_vector_plan(vector)

    rendered = CLAUDE_EXPLORATION_DISPATCH_RENDERER.render(plan, (vector,))
    decoded_prompt = _decode_message_argument(rendered.replacements[vector.id], "prompt")

    assert _has_exact_identity_field(decoded_prompt, "task_id", vector.task.task_id)
    assert _has_exact_identity_field(
        decoded_prompt,
        "role_definition_digest",
        rendered.role_definition_digests[vector.id],
    )
    assert _has_exact_identity_field(
        decoded_prompt, "router_plan_digest", rendered.router_plan_digest
    )


def test_identity_field_recognized_in_codex_rendered_packet() -> None:
    vector = _first_migrated_security_vector()
    plan = _single_vector_plan(vector)

    rendered = CODEX_EXPLORATION_DISPATCH_RENDERER.render(plan, (vector,))
    decoded_prompt = _decode_message_argument(rendered.replacements[vector.id], "message")

    assert _has_exact_identity_field(decoded_prompt, "task_id", vector.task.task_id)
    assert _has_exact_identity_field(
        decoded_prompt,
        "role_definition_digest",
        rendered.role_definition_digests[vector.id],
    )
    assert _has_exact_identity_field(
        decoded_prompt, "router_plan_digest", rendered.router_plan_digest
    )


def test_identity_field_not_found_for_missing_field() -> None:
    prompt_without_task_id = (
        "AutoSkillit typed exploration task packet\n"
        f"router_plan_digest: {'0' * 64}\n"
        f"role_definition_digest: {'1' * 64}\n"
    )

    assert _has_exact_identity_field(prompt_without_task_id, "task_id", "some-task-id") is False


def test_always_active_target_identity_build_succeeds(tmp_path: Path) -> None:
    """Regression test for dd30c7e6e.

    That commit switched identity checks from substring matching to exact-line
    matching against the *raw* projected SKILL.md text. Because the renderer
    embeds every native packet's prompt via ``json.dumps()``, a projected
    always-active, multi-vector skill collapses each packet onto one physical
    line — so exact-line matching against the raw text could never find a
    `task_id:` / `router_plan_digest:` / `role_definition_digest:` line, and
    identity verification would always raise ``SkillContractError``. The
    current decode-once implementation JSON-decodes each packet's message
    argument before checking, so the build must succeed here.
    """
    skill_path = pkg_root() / "skills_extended" / _ALWAYS_ACTIVE_SKILL / "SKILL.md"
    info = _skill_info_from_frontmatter(
        _ALWAYS_ACTIVE_SKILL, SkillSource.BUNDLED_EXTENDED, skill_path
    )
    assert not info.invalidities, info.invalidities
    migrated = [
        vector
        for vector in info.exploration_vectors
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
    ]
    assert len(migrated) == 7

    backend = ClaudeCodeBackend()
    invocation = EffectiveSkillInvocation(
        root=info,
        closure=(info,),
        capability_union=info.uses_capabilities,
        project_root=pkg_root(),
        execution_role=SkillExecutionRole.SESSION,
    )
    projection_context = SkillProjectionContext(
        cwd=pkg_root(),
        invocation=invocation,
        backend=backend,
        resolved_exploration_profile=RepositoryProfileId.AUTOSKILLIT,
        parent_sandbox_mode="read-only",
    )
    document = project_agent_skill_document(info, projection_context)

    add_dir_root = tmp_path / "session"
    skill_dest = add_dir_root / ".claude" / "skills" / _ALWAYS_ACTIVE_SKILL
    skill_dest.mkdir(parents=True)
    (skill_dest / "SKILL.md").write_text(document.content, encoding="utf-8")
    skill_add_dir = validate_add_dir(add_dir_root)

    identity = _build_requested_execution_identity(
        projection_context=projection_context,
        target_name=_ALWAYS_ACTIVE_SKILL,
        skill_add_dirs=(skill_add_dir,),
        effective_backend=backend,
        effective_model="claude-opus-5",
        explicit_resolution=None,
    )

    assert len(identity.children) == 7
    assert {child.task_id for child in identity.children} == {
        vector.task.task_id for vector in migrated
    }
    assert {child.role for child in identity.children} == {
        "semantic-code-navigator",
        "repository-impact-profiler",
    }
