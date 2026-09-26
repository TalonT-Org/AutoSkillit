"""Prescribed shell commands must conform to the installed write-target guards."""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

from autoskillit.core import RepositoryProfileId, SkillExecutionRole, pkg_root
from autoskillit.execution.backends import ClaudeCodeBackend
from autoskillit.hooks import scan_write_targets
from autoskillit.hooks.guards import installation_integrity_guard, write_guard
from autoskillit.recipe._skill_placeholder_parser import extract_bash_blocks
from autoskillit.recipe.io import builtin_recipes_dir, builtin_sub_recipes_dir, load_recipe
from autoskillit.workspace import (
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    project_agent_skill_document,
)
from autoskillit.workspace.session_skills import SkillsDirectoryProvider
from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir
from tests.hooks.conftest import make_hook_event

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_MODEL_PLACEHOLDER = re.compile(r"(?<![$@])\{([A-Za-z_][A-Za-z0-9_-]*)\}")
_BASH_FENCE = re.compile(r"```bash\s*\n(.*?)```", re.DOTALL)
_FENCE = re.compile(r"^[ \t]*```([^\n`]*)\n(.*?)^[ \t]*```[ \t]*$", re.DOTALL | re.MULTILINE)
_SHELL_COMMAND = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*=|"
    r"(?:bash|sh|zsh|git|gh|python[0-9.]*|uv|task|cd|mkdir|cp|mv|rm|"
    r"echo|printf|cat|tee|sed|awk|jq|rg|find|chmod|install|export|source|"
    r"curl|wget|npm|docker|if|for|while|eval|read|test)\b|\./)"
)


def _rendered_skills() -> list[tuple[str, str]]:
    provider = SkillsDirectoryProvider(temp_dir_relpath=".autoskillit/temp")
    backend = ClaudeCodeBackend()
    skills = []
    for root in (bundled_skills_dir(), bundled_skills_extended_dir()):
        for path in sorted(root.glob("*/SKILL.md")):
            info = provider.resolver.resolve(path.parent.name)
            assert info is not None, path
            context = provider.catalog_projection_context(
                EffectiveSkillCatalog(
                    skills=(SkillCatalogEntry.from_skill_info(info),),
                    execution_role=info.execution_role or SkillExecutionRole.SESSION,
                ),
                root,
                backend=backend,
                durable_scripts_root=pkg_root(),
                resolved_exploration_profile=RepositoryProfileId.AUTOSKILLIT,
            )
            skills.append(
                (
                    path.parent.name,
                    project_agent_skill_document(context.skills[0], context).content,
                )
            )
    return skills


_SKILLS = _rendered_skills()
_BASH_CASES = []
for _name, _content in _SKILLS:
    _spans = list(_BASH_FENCE.finditer(_content))
    _blocks = extract_bash_blocks(_content)
    assert [span.group(1) for span in _spans] == _blocks
    for _span, _block in zip(_spans, _blocks, strict=True):
        _line = _content.count("\n", 0, _span.start()) + 1
        _BASH_CASES.append(pytest.param(_block, id=f"{_name}:{_line}"))

_RECIPES = [
    load_recipe(path)
    for root in (builtin_recipes_dir(), builtin_sub_recipes_dir())
    for path in sorted(root.glob("*.yaml"))
]
_RECIPE_CASES = [
    pytest.param(step.with_args["cmd"], id=f"{recipe.name}:{name}")
    for recipe in _RECIPES
    for name, step in recipe.steps.items()
    if step.tool == "run_cmd"
]


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".autoskillit").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(tmp_path))
    return tmp_path


def _guard_denials(event: dict, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    denials = []
    for guard in (installation_integrity_guard, write_guard):
        output = io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
            patch.setattr(sys, "stdout", output)
            try:
                guard.main()
            except SystemExit as exc:
                assert exc.code in (None, 0, 2), exc.code
        if output.getvalue():
            decision = json.loads(output.getvalue())["hookSpecificOutput"]
            if decision["permissionDecision"] == "deny":
                denials.append(f"{guard.__name__}: {decision['permissionDecisionReason']}")
    return denials


def _assert_allowed(command: str, tool: str, checkout: Path, monkeypatch: pytest.MonkeyPatch):
    event = make_hook_event(
        tool=tool,
        command=command,
        payload_cwd=str(checkout),
        tool_cwd=str(checkout) if tool == "run_cmd" else None,
    )
    denials = _guard_denials(event, monkeypatch)
    scan = scan_write_targets(command, str(checkout))
    assert not denials, (
        f"targets={scan.targets}; {'; '.join(denials)}; "
        "see skills_extended/AGENTS.md § Literal write targets"
    )


def _bad_fence_labels(content: str) -> list[tuple[int, str]]:
    failures = []
    for match in _FENCE.finditer(content):
        label = match.group(1).strip()
        executable = next(
            (
                line.strip()
                for line in match.group(2).splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ),
            "",
        )
        if label in {"sh", "shell", "zsh", "console", "shell-session"} or (
            not label and _SHELL_COMMAND.match(executable)
        ):
            failures.append((content.count("\n", 0, match.start()) + 1, label))
    return failures


def test_corpora_are_nonempty():
    assert _SKILLS and _BASH_CASES
    assert _RECIPES and _RECIPE_CASES


@pytest.mark.parametrize("name,content", _SKILLS, ids=[name for name, _ in _SKILLS])
def test_skill_projection_and_fence_labels(name: str, content: str):
    assert "{{AUTOSKILLIT_" not in content, name
    assert not _bad_fence_labels(content), f"{name}: {_bad_fence_labels(content)}"


@pytest.mark.parametrize("block", _BASH_CASES)
def test_skill_bash_write_targets(block: str, checkout: Path, monkeypatch: pytest.MonkeyPatch):
    command = _MODEL_PLACEHOLDER.sub(lambda match: f"sample_{match[1].lower()}", block)
    _assert_allowed(command, "Bash", checkout, monkeypatch)


@pytest.mark.parametrize("command", _RECIPE_CASES)
def test_recipe_run_cmd_write_targets(
    command: str, checkout: Path, monkeypatch: pytest.MonkeyPatch
):
    assert "{{AUTOSKILLIT_" not in command
    command = re.sub(r"\$\{\{.*?\}\}", "sample", command, flags=re.DOTALL)
    _assert_allowed(command, "run_cmd", checkout, monkeypatch)


@pytest.mark.parametrize(
    "command",
    [
        'F=".autoskillit/temp/x/y.md"; echo hi > "$F"',
        'echo hi > ".autoskillit/temp/x/$(date +%s).md"',
    ],
)
def test_unresolved_write_negative_controls(
    command: str, checkout: Path, monkeypatch: pytest.MonkeyPatch
):
    event = make_hook_event(tool="Bash", command=command, payload_cwd=str(checkout))
    assert len(_guard_denials(event, monkeypatch)) == 2


@pytest.mark.parametrize("label", ["sh", "shell", "zsh", "console", "shell-session", ""])
def test_shell_fence_label_negative_control(label: str):
    assert _bad_fence_labels(f"```{label}\ngh issue list\n```") == [(1, label)]
