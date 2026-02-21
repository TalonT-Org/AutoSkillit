"""Tests for pipeline script discovery from .autoskillit/scripts/."""

from __future__ import annotations

from pathlib import Path

import yaml

from autoskillit.script_loader import list_scripts, load_script

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
            "with": {"skill_command": '/autoskillit:make-plan ${{ inputs.task }}', "cwd": "."},
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
                "skill_command": '/autoskillit:investigate ${{ inputs.problem }}',
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
    (scripts_dir / "implementation.yaml").write_text(
        yaml.dump(SCRIPT_A, default_flow_style=False)
    )
    (scripts_dir / "investigate.yaml").write_text(
        yaml.dump(SCRIPT_B, default_flow_style=False)
    )
    return scripts_dir


class TestListScripts:
    # SL1
    def test_empty_when_dir_missing(self, tmp_path: Path) -> None:
        """list_scripts returns empty list when .autoskillit/scripts/ doesn't exist."""
        assert list_scripts(tmp_path) == []

    # SL2
    def test_discovers_yaml_files(self, tmp_path: Path) -> None:
        """list_scripts discovers .yaml files in .autoskillit/scripts/."""
        _make_scripts_dir(tmp_path)
        scripts = list_scripts(tmp_path)
        names = {s.name for s in scripts}
        assert "implementation" in names
        assert "investigate-fix" in names

    # SL3
    def test_ignores_non_yaml_and_malformed(self, tmp_path: Path) -> None:
        """list_scripts ignores non-yaml files and malformed yaml."""
        scripts_dir = _make_scripts_dir(tmp_path)
        (scripts_dir / "readme.txt").write_text("not a yaml script")
        (scripts_dir / "broken.yaml").write_text(":: invalid yaml {{[")
        scripts = list_scripts(tmp_path)
        names = {s.name for s in scripts}
        assert "readme" not in names
        assert "broken" not in names
        assert len(scripts) == 2  # only the two valid ones

    # SL4
    def test_extracts_summary_field(self, tmp_path: Path) -> None:
        """list_scripts extracts summary field from YAML."""
        _make_scripts_dir(tmp_path)
        scripts = list_scripts(tmp_path)
        impl = next(s for s in scripts if s.name == "implementation")
        assert impl.summary == SCRIPT_A["summary"]

    # SL5
    def test_empty_summary_when_absent(self, tmp_path: Path) -> None:
        """list_scripts returns empty summary when field absent."""
        _make_scripts_dir(tmp_path)
        scripts = list_scripts(tmp_path)
        inv = next(s for s in scripts if s.name == "investigate-fix")
        assert inv.summary == ""

    # SL8
    def test_sorted_by_name(self, tmp_path: Path) -> None:
        """list_scripts sorts results by name."""
        _make_scripts_dir(tmp_path)
        scripts = list_scripts(tmp_path)
        names = [s.name for s in scripts]
        assert names == sorted(names)


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
