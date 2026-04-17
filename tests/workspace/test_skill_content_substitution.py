"""Tests for SkillsDirectoryProvider.get_skill_content placeholder substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


class _StubInfo:
    def __init__(self, path: Path, name: str) -> None:
        self.path = path
        self.name = name


def _make_synth_skill_md(tmp_path: Path, name: str, body: str) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    md = skill_dir / "SKILL.md"
    md.write_text(
        f"---\nname: {name}\ndescription: synthetic\n---\n{body}\n",
        encoding="utf-8",
    )
    return md


def _provider_with_synth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    body: str,
    *,
    temp_dir_relpath: str = ".autoskillit/temp",
) -> tuple[SkillsDirectoryProvider, _StubInfo]:
    provider = SkillsDirectoryProvider(temp_dir_relpath=temp_dir_relpath)
    info = _StubInfo(_make_synth_skill_md(tmp_path, name, body), name)
    monkeypatch.setattr(provider._resolver, "resolve", lambda n: info if n == name else None)
    return provider, info


def test_get_skill_content_substitutes_default_temp_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider, _ = _provider_with_synth(
        monkeypatch, tmp_path, "synth_default", "Write to {{AUTOSKILLIT_TEMP}}/foo/output.md"
    )
    content = provider.get_skill_content("synth_default", gated=False)
    assert "{{AUTOSKILLIT_TEMP}}" not in content
    assert ".autoskillit/temp/foo/output.md" in content


def test_get_skill_content_substitutes_custom_temp_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider, _ = _provider_with_synth(
        monkeypatch,
        tmp_path,
        "synth_custom",
        "Write to {{AUTOSKILLIT_TEMP}}/foo",
        temp_dir_relpath=".build/scratch",
    )
    content = provider.get_skill_content("synth_custom", gated=False)
    assert ".build/scratch/foo" in content


def test_get_skill_content_substitution_runs_after_gating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider, _ = _provider_with_synth(
        monkeypatch, tmp_path, "synth_gated", "Write to {{AUTOSKILLIT_TEMP}}/x"
    )
    content = provider.get_skill_content("synth_gated", gated=True)
    assert "disable-model-invocation: true" in content
    assert ".autoskillit/temp/x" in content


def test_get_skill_content_no_placeholder_no_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider, _ = _provider_with_synth(monkeypatch, tmp_path, "synth_plain", "Hello world.")
    content = provider.get_skill_content("synth_plain", gated=False)
    assert "Hello world." in content


def test_get_skill_content_rejects_yaml_unsafe_temp_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="YAML-unsafe"):
        SkillsDirectoryProvider(temp_dir_relpath="bad\nvalue")
    with pytest.raises(ValueError, match="YAML-unsafe"):
        SkillsDirectoryProvider(temp_dir_relpath="bad: value")


def test_init_session_writes_substituted_skill_md_for_real_skill(
    tmp_path: Path,
) -> None:
    """End-to-end: init_session must write SKILL.md with placeholder substituted.

    Picks a real bundled Tier 2/3 skill that contains ``{{AUTOSKILLIT_TEMP}}``,
    runs init_session in cook_session mode, and verifies the ephemeral copy is
    substituted.
    """
    provider = SkillsDirectoryProvider(temp_dir_relpath=".autoskillit/temp")
    candidates = sorted(
        (
            s
            for s in provider.list_skills()
            if "{{AUTOSKILLIT_TEMP}}" in s.path.read_text(encoding="utf-8")
        ),
        key=lambda s: s.name,
    )
    assert candidates, "expected at least one bundled skill with the placeholder"
    target = candidates[0]

    ephemeral_root = tmp_path / "ephemeral"
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=ephemeral_root)
    validated = mgr.init_session(session_id="sess-1", cook_session=True)

    written = Path(str(validated.path)) / ".claude" / "skills" / target.name / "SKILL.md"
    assert written.exists(), f"ephemeral SKILL.md not written at {written}"
    text = written.read_text()
    assert "{{AUTOSKILLIT_TEMP}}" not in text, (
        "ephemeral SKILL.md must have the placeholder substituted"
    )
    assert ".autoskillit/temp" in text
