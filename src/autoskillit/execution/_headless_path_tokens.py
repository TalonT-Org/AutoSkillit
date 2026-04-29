"""Path-token extraction and validation for headless Claude session output."""

from __future__ import annotations

import os
import re

from autoskillit.core import get_logger, load_yaml, pkg_root

logger = get_logger(__name__)

_WORKTREE_PATH_PATTERN: re.Pattern[str] = re.compile(r"^worktree_path\s*=\s*(.+)$", re.MULTILINE)


def _extract_worktree_path(assistant_messages: list[str]) -> str | None:
    """Return the last absolute path emitted as worktree_path=<value>."""
    last: str | None = None
    for msg in assistant_messages:
        m = _WORKTREE_PATH_PATTERN.search(msg)
        if m:
            candidate = m.group(1).strip()
            if os.path.isabs(candidate):
                last = candidate
    return last


# Intentionally excluded: these tokens are handled by dedicated extractors
# (_WORKTREE_PATH_PATTERN for worktree_path; branch_name is used as a string,
# not for path-contamination checks).
_INTENTIONALLY_EXCLUDED_PATH_TOKENS: frozenset[str] = frozenset(
    {
        "worktree_path",
        "branch_name",
    }
)


def _build_path_token_set() -> frozenset[str]:
    """Derive the set of file-path output token names from skill_contracts.yaml.

    This replaces the manually-maintained frozenset and ensures new skills added
    to the contracts file are automatically included in path-contamination checks.
    Falls back to an empty frozenset if the manifest is unavailable (e.g., in
    test environments where the package is not installed).

    Filters outputs where type starts with "file_path" (covers both "file_path"
    and "file_path_list"). The outputs section in skill_contracts.yaml is a list
    of dicts with "name" and "type" keys — not a mapping.

    Loads the YAML directly via L0 core utilities to avoid an upward L1→L2 import.
    """
    try:
        manifest_path = pkg_root() / "recipe" / "skill_contracts.yaml"
        manifest = load_yaml(manifest_path)
        if not isinstance(manifest, dict):
            logger.debug(
                "skill_contracts.yaml is empty or non-dict; _OUTPUT_PATH_TOKENS will be empty"
            )
            return frozenset()
        result = (
            frozenset(
                out["name"]
                for skill_data in manifest.get("skills", {}).values()
                for out in skill_data.get("outputs", [])
                if isinstance(out, dict) and out.get("type", "").startswith("file_path")
            )
            - _INTENTIONALLY_EXCLUDED_PATH_TOKENS
        )
        logger.debug("_OUTPUT_PATH_TOKENS derived from contracts", count=len(result))
        return result
    except FileNotFoundError:
        logger.debug("skill_contracts.yaml not found; _OUTPUT_PATH_TOKENS will be empty")
        return frozenset()
    except Exception:
        logger.warning("Failed to derive _OUTPUT_PATH_TOKENS from contracts YAML", exc_info=True)
        return frozenset()


_OUTPUT_PATH_TOKENS: frozenset[str] = _build_path_token_set()

_OUTPUT_PATH_PATTERN: re.Pattern[str] = (
    re.compile(
        r"^(" + "|".join(re.escape(t) for t in sorted(_OUTPUT_PATH_TOKENS)) + r")\s*=\s*(.+)$",
        re.MULTILINE,
    )
    if _OUTPUT_PATH_TOKENS
    else re.compile(r"(?!)")  # never-matches sentinel when token set is empty
)


def _extract_output_paths(assistant_messages: list[str]) -> dict[str, str]:
    """Extract structured output path tokens from session output."""
    paths: dict[str, str] = {}
    for msg in assistant_messages:
        for m in _OUTPUT_PATH_PATTERN.finditer(msg):
            token, value = m.group(1), m.group(2).strip()
            if os.path.isabs(value):
                paths[token] = value
    return paths


def _validate_output_paths(
    extracted_paths: dict[str, str],
    cwd: str,
) -> str | None:
    """Return a diagnostic string if any path is outside cwd, else None."""
    if not os.path.isabs(cwd) or cwd == "/":
        return None
    cwd_prefix = cwd.rstrip("/") + "/"
    violations = []
    for token, path in extracted_paths.items():
        if not path.startswith(cwd_prefix) and path != cwd.rstrip("/"):
            violations.append(f"{token} '{path}' is outside session cwd '{cwd}'")
    return "; ".join(violations) if violations else None
