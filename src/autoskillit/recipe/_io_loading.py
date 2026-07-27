"""Declaration-preserving recipe document loading and placeholder substitution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoskillit.core import fast_loads, get_logger, load_yaml, pkg_root
from autoskillit.recipe._contracts_types import INPUT_REF_RE

logger = get_logger(__name__)

_TEMP_PLACEHOLDER = "{{AUTOSKILLIT_TEMP}}"
_SCRIPTS_PLACEHOLDER = "{{AUTOSKILLIT_SCRIPTS}}"


def substitute_temp_placeholder(text: str, temp_dir_relpath: str) -> str:
    """Replace the temp placeholder after rejecting YAML-unsafe path text."""
    if "\n" in temp_dir_relpath or ": " in temp_dir_relpath:
        raise ValueError(f"temp_dir_relpath is YAML-unsafe: {temp_dir_relpath!r}")
    return text.replace(_TEMP_PLACEHOLDER, temp_dir_relpath)


def substitute_scripts_placeholder(text: str) -> str:
    """Replace the scripts placeholder with the bundled recipe scripts path."""
    if _SCRIPTS_PLACEHOLDER not in text:
        return text
    scripts_dir = pkg_root() / "recipes" / "scripts"
    return text.replace(_SCRIPTS_PLACEHOLDER, str(scripts_dir))


def assert_no_raw_placeholders(
    text: str,
    *,
    context: str = "",
    hidden_ingredient_names: frozenset[str] | None = None,
) -> None:
    """Reject unresolved host or hidden-ingredient placeholders at delivery."""
    for placeholder in (_TEMP_PLACEHOLDER, _SCRIPTS_PLACEHOLDER):
        if placeholder in text:
            raise ValueError(
                f"Unresolved {placeholder} in recipe content"
                + (f" ({context})" if context else "")
            )
    if hidden_ingredient_names:
        for match in INPUT_REF_RE.finditer(text):
            name = match.group(1)
            if name in hidden_ingredient_names:
                raise ValueError(
                    f"Unresolved hidden ingredient template ${{{{ inputs.{name} }}}} "
                    "in recipe content" + (f" ({context})" if context else "")
                )


def load_recipe_dict(
    yaml_path: Path,
    *,
    raw_text: str | None = None,
    temp_dir_relpath: str | None = None,
) -> dict[str, Any]:
    """Load an effective recipe mapping, preferring a fresh compiled sibling."""
    effective, _declared = load_recipe_dict_with_declarations(
        yaml_path,
        raw_text=raw_text,
        temp_dir_relpath=temp_dir_relpath,
    )
    return effective


def _substitute_recipe_values(
    value: Any,
    *,
    temp_dir_relpath: str | None,
) -> Any:
    if isinstance(value, str):
        resolved = (
            substitute_temp_placeholder(value, temp_dir_relpath)
            if temp_dir_relpath is not None
            else value
        )
        return substitute_scripts_placeholder(resolved)
    if isinstance(value, dict):
        return {
            key: _substitute_recipe_values(item, temp_dir_relpath=temp_dir_relpath)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _substitute_recipe_values(item, temp_dir_relpath=temp_dir_relpath) for item in value
        ]
    return value


def load_recipe_dict_with_declarations(
    yaml_path: Path,
    *,
    raw_text: str | None = None,
    temp_dir_relpath: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load aligned effective and declared mappings from JSON or YAML."""
    json_path = yaml_path.with_suffix(".json")
    try:
        if json_path.stat().st_mtime_ns >= yaml_path.stat().st_mtime_ns:
            text = json_path.read_text(encoding="utf-8")
            data = fast_loads(text)
            if isinstance(data, dict):
                return (
                    _substitute_recipe_values(
                        data,
                        temp_dir_relpath=temp_dir_relpath,
                    ),
                    data,
                )
            logger.warning(
                "Pre-compiled JSON is not a mapping, falling back to YAML: %s", json_path
            )
    except json.JSONDecodeError:
        logger.warning("Pre-compiled JSON is corrupt, falling back to YAML: %s", json_path)
    except (FileNotFoundError, OSError):
        pass
    if raw_text is None:
        raw_text = yaml_path.read_text(encoding="utf-8")
    data = load_yaml(raw_text)
    if not isinstance(data, dict):
        raise ValueError(f"Recipe file must contain a YAML mapping: {yaml_path}")
    return (
        _substitute_recipe_values(data, temp_dir_relpath=temp_dir_relpath),
        data,
    )
