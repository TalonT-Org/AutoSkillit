"""Tests for pipeline script discovery from .autoskillit/scripts/."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.script_loader import (
    _extract_frontmatter,
    _parse_script_metadata,
    list_scripts,
    load_script,
)

SCRIPT_A = {
    "name": "implementation",
    "description": "Plan and implement a task end-to-end.",
    "summary": "make-plan > review > for each part: dry-walk > implement > test > merge",
    "inputs": {
        "task": {"description": "What to implement", "required": True},
        "base_branch": {"description": "Branch to merge into", "default": "main"},
    },
    "steps": {
        "plan": {
            "tool": "run_skill",
            "with": {"skill_command": "/autoskillit:make-plan ${{ inputs.task }}", "cwd": "."},
            "on_success": "done",
            "on_failure": "escalate",
        },
        "done": {"action": "stop", "message": "Done."},
        "escalate": {"action": "stop", "message": "Failed."},
    },
}

SCRIPT_B = {
    "name": "investigate-fix",
    "description": "Investigate and fix a bug.",
    "inputs": {
        "problem": {"description": "Error description", "required": True},
    },
    "steps": {
        "investigate": {
            "tool": "run_skill",
            "with": {
                "skill_command": "/autoskillit:investigate ${{ inputs.problem }}",
                "cwd": ".",
            },
            "on_success": "done",
            "on_failure": "escalate",
        },
        "done": {"action": "stop", "message": "Done."},
        "escalate": {"action": "stop", "message": "Failed."},
    },
}


def _make_scripts_dir(tmp_path: Path) -> Path:
    """Create .autoskillit/scripts/ with two test YAML files."""
    scripts_dir = tmp_path / ".autoskillit" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "implementation.yaml").write_text(yaml.dump(SCRIPT_A, default_flow_style=False))
    (scripts_dir / "investigate.yaml").write_text(yaml.dump(SCRIPT_B, default_flow_style=False))
    return scripts_dir


class TestListScripts:
    # SL1
    def test_empty_when_dir_missing(self, tmp_path: Path) -> None:
        """list_scripts returns empty result when .autoskillit/scripts/ doesn't exist."""
        result = list_scripts(tmp_path)
        assert result.items == []
        assert result.errors == []

    # SL2
    def test_discovers_yaml_files(self, tmp_path: Path) -> None:
        """list_scripts discovers .yaml files in .autoskillit/scripts/."""
        _make_scripts_dir(tmp_path)
        scripts = list_scripts(tmp_path).items
        names = {s.name for s in scripts}
        assert "implementation" in names
        assert "investigate-fix" in names

    # SL3
    def test_ignores_non_yaml_and_reports_malformed(self, tmp_path: Path) -> None:
        """list_scripts ignores non-yaml files and reports malformed yaml as errors."""
        scripts_dir = _make_scripts_dir(tmp_path)
        (scripts_dir / "readme.txt").write_text("not a yaml script")
        (scripts_dir / "broken.yaml").write_text(":: invalid yaml {{[")
        result = list_scripts(tmp_path)
        names = {s.name for s in result.items}
        assert "readme" not in names
        assert "broken" not in names
        assert len(result.items) == 2  # only the two valid ones
        assert len(result.errors) == 1  # broken.yaml reported
        assert "broken.yaml" in result.errors[0].path.name

    # SL4
    def test_extracts_summary_field(self, tmp_path: Path) -> None:
        """list_scripts extracts summary field from YAML."""
        _make_scripts_dir(tmp_path)
        scripts = list_scripts(tmp_path).items
        impl = next(s for s in scripts if s.name == "implementation")
        assert impl.summary == SCRIPT_A["summary"]

    # SL5
    def test_empty_summary_when_absent(self, tmp_path: Path) -> None:
        """list_scripts returns empty summary when field absent."""
        _make_scripts_dir(tmp_path)
        scripts = list_scripts(tmp_path).items
        inv = next(s for s in scripts if s.name == "investigate-fix")
        assert inv.summary == ""

    # SL8
    def test_sorted_by_name(self, tmp_path: Path) -> None:
        """list_scripts sorts results by name."""
        _make_scripts_dir(tmp_path)
        scripts = list_scripts(tmp_path).items
        names = [s.name for s in scripts]
        assert names == sorted(names)

    def test_discovers_frontmatter_format(self, tmp_path: Path) -> None:
        """Scripts in YAML frontmatter format must be discovered."""
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "pipeline.yaml").write_text(
            "---\nname: my-pipeline\ndescription: A pipeline\n"
            "summary: plan > implement\n---\n\n# Pipeline\nDo stuff.\n"
        )
        result = list_scripts(tmp_path)
        assert len(result.items) == 1
        assert result.items[0].name == "my-pipeline"
        assert result.items[0].summary == "plan > implement"

    def test_discovers_frontmatter_with_adversarial_body(self, tmp_path: Path) -> None:
        """Scripts with YAML-like Markdown bodies must be discovered, not errored."""
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "pipeline.yaml").write_text(
            "---\nname: adv-pipeline\ndescription: Test\n---\n\n"
            "# Steps\n\nSETUP:\n  - item: value\n  - key: other\n"
        )
        result = list_scripts(tmp_path)
        assert len(result.items) == 1
        assert len(result.errors) == 0
        assert result.items[0].name == "adv-pipeline"

    def test_reports_errors(self, tmp_path: Path) -> None:
        """Malformed scripts must produce error reports, not silent skips."""
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "good.yaml").write_text("name: good\ndescription: Valid\n")
        (scripts_dir / "bad.yaml").write_text(":: invalid {{[\n")
        result = list_scripts(tmp_path)
        assert len(result.items) == 1
        assert len(result.errors) == 1
        assert "bad.yaml" in result.errors[0].path.name


class TestParseScriptMetadata:
    def test_single_document(self, tmp_path: Path) -> None:
        """Standard YAML without frontmatter."""
        path = tmp_path / "script.yaml"
        path.write_text("name: my-script\ndescription: A script\nsummary: do stuff\n")
        info = _parse_script_metadata(path)
        assert info.name == "my-script"
        assert info.description == "A script"
        assert info.summary == "do stuff"

    def test_frontmatter_format(self, tmp_path: Path) -> None:
        """YAML frontmatter with --- delimiters and Markdown body."""
        path = tmp_path / "script.yaml"
        path.write_text(
            "---\nname: fm-script\ndescription: Frontmatter\n---\n\n"
            "# Title\n\nKey: value\n- list item\n"
        )
        info = _parse_script_metadata(path)
        assert info.name == "fm-script"
        assert info.description == "Frontmatter"

    def test_frontmatter_with_steps(self, tmp_path: Path) -> None:
        """YAML frontmatter where metadata block includes steps."""
        path = tmp_path / "script.yaml"
        path.write_text(
            "---\nname: step-script\ndescription: Has steps\n"
            "steps:\n  plan:\n    tool: run_skill\n---\n"
        )
        info = _parse_script_metadata(path)
        assert info.name == "step-script"

    def test_frontmatter_with_yaml_like_body(self, tmp_path: Path) -> None:
        """Frontmatter parsing must succeed even when body has YAML-like syntax."""
        path = tmp_path / "script.yaml"
        path.write_text(
            "---\n"
            "name: pipeline\n"
            "description: A pipeline\n"
            "---\n\n"
            "# Implementation Pipeline\n\n"
            "## Phase 1: Planning\n"
            "SETUP:\n"
            "  - project_dir = /home/user/project\n"
            "  - work_dir = /home/user/work\n\n"
            "PIPELINE:\n"
            "0. Run make-plan with the task:\n"
            "   task: ${{ inputs.task }}\n"
        )
        info = _parse_script_metadata(path)
        assert info.name == "pipeline"
        assert info.description == "A pipeline"

    def test_rejects_empty_file(self, tmp_path: Path) -> None:
        """Empty file raises ValueError."""
        path = tmp_path / "empty.yaml"
        path.write_text("")
        with pytest.raises(ValueError, match="mapping"):
            _parse_script_metadata(path)

    def test_rejects_non_mapping(self, tmp_path: Path) -> None:
        """File with YAML list raises ValueError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="mapping"):
            _parse_script_metadata(path)

    def test_rejects_missing_name(self, tmp_path: Path) -> None:
        """File without name field raises ValueError."""
        path = tmp_path / "noname.yaml"
        path.write_text("description: No name here\n")
        with pytest.raises(ValueError, match="name"):
            _parse_script_metadata(path)


class TestExtractFrontmatter:
    def test_plain_yaml_passthrough(self) -> None:
        """Text without --- prefix is returned unchanged."""
        text = "name: foo\ndescription: bar\n"
        assert _extract_frontmatter(text) == text

    def test_frontmatter_extracts_metadata(self) -> None:
        """Text between --- delimiters is extracted."""
        text = "---\nname: foo\n---\n\n# Body\n"
        assert _extract_frontmatter(text) == "name: foo"

    def test_frontmatter_discards_body(self) -> None:
        """Everything after closing --- is discarded."""
        text = "---\nname: foo\n---\n\nSETUP:\n  - bad: yaml\n"
        result = _extract_frontmatter(text)
        assert "SETUP" not in result
        assert "bad" not in result

    def test_frontmatter_missing_close_raises(self) -> None:
        """Missing closing --- raises ValueError."""
        text = "---\nname: foo\nno closing delimiter\n"
        with pytest.raises(ValueError):
            _extract_frontmatter(text)


class TestLoadScript:
    # SL6
    def test_returns_raw_yaml(self, tmp_path: Path) -> None:
        """load_script returns raw YAML content for existing script name."""
        _make_scripts_dir(tmp_path)
        content = load_script(tmp_path, "implementation")
        assert content is not None
        parsed = yaml.safe_load(content)
        assert parsed["name"] == "implementation"

    # SL7
    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        """load_script returns None for nonexistent script name."""
        _make_scripts_dir(tmp_path)
        assert load_script(tmp_path, "nonexistent") is None


# ---------------------------------------------------------------------------
# TestScriptVersion: ScriptInfo includes version from autoskillit_version field
# ---------------------------------------------------------------------------


class TestScriptVersion:
    """ScriptInfo includes version from autoskillit_version field."""

    # SV1: ScriptInfo.version is None when field absent
    def test_version_none_when_absent(self, tmp_path: Path) -> None:
        """_parse_script_metadata sets version=None when autoskillit_version is absent."""
        path = tmp_path / "script.yaml"
        path.write_text("name: my-script\ndescription: A script\n")
        info = _parse_script_metadata(path)
        assert info.version is None

    # SV2: ScriptInfo.version is "0.2.0" when field present
    def test_version_set_when_present(self, tmp_path: Path) -> None:
        """_parse_script_metadata reads autoskillit_version and stores it as version."""
        path = tmp_path / "script.yaml"
        path.write_text('name: my-script\ndescription: A script\nautoskillit_version: "0.2.0"\n')
        info = _parse_script_metadata(path)
        assert info.version == "0.2.0"

    # SV3: list_scripts returns version in ScriptInfo items
    def test_list_scripts_includes_version(self, tmp_path: Path) -> None:
        """list_scripts propagates autoskillit_version into the returned ScriptInfo items."""
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "versioned.yaml").write_text(
            "name: versioned-script\n"
            "description: Has version\n"
            'autoskillit_version: "0.2.0"\n'
            "steps:\n"
            "  do_it:\n"
            "    tool: run_cmd\n"
            "    on_success: done\n"
            "  done:\n"
            "    action: stop\n"
            "    message: Done.\n"
        )
        (scripts_dir / "unversioned.yaml").write_text(
            "name: unversioned-script\ndescription: No version\n"
        )
        result = list_scripts(tmp_path)
        by_name = {s.name: s for s in result.items}
        assert by_name["versioned-script"].version == "0.2.0"
        assert by_name["unversioned-script"].version is None
