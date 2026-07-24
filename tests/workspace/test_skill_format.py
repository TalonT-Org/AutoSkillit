"""Unit tests for skill frontmatter validation functions."""

from __future__ import annotations

import pytest

from autoskillit.core import SkillExecutionRole
from autoskillit.workspace.skill_format import (
    SkillFrontmatterParseResult,
    parse_frontmatter_content,
    read_skill_frontmatter,
    validate_skill_frontmatter,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


class TestValidateSkillFrontmatter:
    def test_valid_frontmatter_returns_no_errors(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "my-skill", "description": "A skill"}, "my-skill"
        )
        assert result == []

    def test_missing_name_returns_error(self) -> None:
        result = validate_skill_frontmatter({"description": "A skill"}, "my-skill")
        assert any("name" in err for err in result)

    def test_missing_description_returns_error(self) -> None:
        result = validate_skill_frontmatter({"name": "my-skill"}, "my-skill")
        assert any("description" in err for err in result)

    def test_empty_frontmatter_returns_two_errors(self) -> None:
        result = validate_skill_frontmatter({}, "my-skill")
        assert len(result) >= 2

    def test_name_uppercase_rejected(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "MySkill", "description": "A skill"}, "MySkill"
        )
        assert any("lowercase" in err for err in result)

    def test_name_spaces_rejected(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "my skill", "description": "A skill"}, "my skill"
        )
        assert any("lowercase" in err for err in result)

    def test_name_underscore_rejected(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "my_skill", "description": "A skill"}, "my_skill"
        )
        assert any("lowercase" in err for err in result)

    def test_name_too_long_rejected(self) -> None:
        long_name = "a" * 65
        result = validate_skill_frontmatter(
            {"name": long_name, "description": "A skill"}, long_name
        )
        assert any("64" in err for err in result)

    def test_name_directory_mismatch_rejected(self) -> None:
        result = validate_skill_frontmatter({"name": "foo", "description": "A skill"}, "bar")
        assert any("foo" in err and "bar" in err for err in result)

    def test_description_too_long_rejected(self) -> None:
        long_desc = "x" * 1025
        result = validate_skill_frontmatter(
            {"name": "my-skill", "description": long_desc}, "my-skill"
        )
        assert any("1024" in err for err in result)

    def test_description_html_angle_brackets_rejected(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "my-skill", "description": "<script>"}, "my-skill"
        )
        assert any("<" in err or ">" in err for err in result)

    def test_extra_fields_allowed(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "x", "description": "y", "categories": ["foo"]}, "x"
        )
        assert result == []


class TestParseFrontmatterContent:
    def test_parse_frontmatter_content_valid(self) -> None:
        content = "---\nname: my-skill\ndescription: A skill\n---\nBody"
        result = parse_frontmatter_content(content)
        assert isinstance(result, SkillFrontmatterParseResult)
        assert result.is_valid
        assert result.error is None
        assert result.data == {"name": "my-skill", "description": "A skill"}
        assert result.execution_role is SkillExecutionRole.SESSION
        assert result.frontmatter_text == "name: my-skill\ndescription: A skill"
        assert result.body == "Body"
        assert result.content == content

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("session", SkillExecutionRole.SESSION),
            ("orchestrator", SkillExecutionRole.ORCHESTRATOR),
            ("fleet", SkillExecutionRole.FLEET),
        ],
    )
    def test_parse_frontmatter_content_returns_typed_explicit_role(
        self, role: str, expected: SkillExecutionRole
    ) -> None:
        result = parse_frontmatter_content(
            f"---\nname: my-skill\nexecution_role: {role}\n---\nBody"
        )

        assert result.is_valid
        assert result.execution_role is expected
        assert result.data is not None
        assert result.data["execution_role"] == role

    @pytest.mark.parametrize(
        ("content", "error"),
        [
            ("Just plain text without frontmatter", "missing_opening_delimiter"),
            ("---\nname: my-skill\nBody", "missing_closing_delimiter"),
            ("---\nname: [unterminated\n---\nBody", "malformed_yaml"),
            ("---\n- one\n- two\n---\nBody", "non_mapping"),
            (
                "---\nname: my-skill\nexecution_role: interactive\n---\nBody",
                "invalid_execution_role",
            ),
            (
                "---\nname: my-skill\nexecution_role: [session]\n---\nBody",
                "invalid_execution_role",
            ),
        ],
    )
    def test_invalid_frontmatter_has_typed_failure(self, content: str, error: str) -> None:
        result = parse_frontmatter_content(content)
        assert isinstance(result, SkillFrontmatterParseResult)
        assert not result.is_valid
        assert result.error == error
        assert result.data is None
        assert result.execution_role is None
        assert result.content == content

    def test_unreadable_frontmatter_is_distinct(self, tmp_path) -> None:
        result = read_skill_frontmatter(tmp_path / "missing" / "SKILL.md")
        assert isinstance(result, SkillFrontmatterParseResult)
        assert not result.is_valid
        assert result.error == "unreadable"
        assert result.data is None
        assert result.execution_role is None


class TestWritePathsValidation:
    """Tests for write_paths validation in validate_skill_frontmatter."""

    def test_valid_write_paths(self) -> None:
        fm = {
            "name": "skill-a",
            "description": "A skill.",
            "write_paths": ["{{AUTOSKILLIT_TEMP}}/skill-a/"],
        }
        assert validate_skill_frontmatter(fm, "skill-a") == []

    def test_write_paths_not_list(self) -> None:
        fm = {"name": "skill-a", "description": "A skill.", "write_paths": "bad"}
        errors = validate_skill_frontmatter(fm, "skill-a")
        assert any("list" in e for e in errors)

    def test_write_paths_traversal_rejected(self) -> None:
        fm = {
            "name": "skill-a",
            "description": "A skill.",
            "write_paths": ["{{AUTOSKILLIT_TEMP}}/../etc/"],
        }
        errors = validate_skill_frontmatter(fm, "skill-a")
        assert any(".." in e for e in errors)

    def test_write_paths_wrong_prefix_rejected(self) -> None:
        fm = {
            "name": "skill-a",
            "description": "A skill.",
            "write_paths": ["/absolute/path/"],
        }
        errors = validate_skill_frontmatter(fm, "skill-a")
        assert any("AUTOSKILLIT_TEMP" in e for e in errors)

    def test_write_paths_absent_is_valid(self) -> None:
        fm = {"name": "skill-a", "description": "A skill."}
        assert validate_skill_frontmatter(fm, "skill-a") == []

    def test_write_paths_resolved_prefix_accepted(self) -> None:
        fm = {
            "name": "skill-a",
            "description": "A skill.",
            "write_paths": [".autoskillit/temp/skill-a/"],
        }
        assert validate_skill_frontmatter(fm, "skill-a") == []
