"""Unit tests for skill frontmatter validation functions."""

from __future__ import annotations

import pytest

from autoskillit.workspace.skill_format import (
    parse_frontmatter_content,
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
        assert len(result) == 2

    def test_name_uppercase_rejected(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "MySkill", "description": "A skill"}, "MySkill"
        )
        assert any("^[a-z0-9-]+$" in err for err in result)

    def test_name_spaces_rejected(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "my skill", "description": "A skill"}, "my skill"
        )
        assert any("^[a-z0-9-]+$" in err for err in result)

    def test_name_underscore_rejected(self) -> None:
        result = validate_skill_frontmatter(
            {"name": "my_skill", "description": "A skill"}, "my_skill"
        )
        assert any("^[a-z0-9-]+$" in err for err in result)

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
        assert result["name"] == "my-skill"
        assert result["description"] == "A skill"

    def test_parse_frontmatter_content_no_frontmatter(self) -> None:
        content = "Just plain text without frontmatter"
        result = parse_frontmatter_content(content)
        assert result == {}

    def test_parse_frontmatter_content_malformed_yaml(self) -> None:
        content = "---\n: invalid\n---\nBody"
        result = parse_frontmatter_content(content)
        assert result == {}
