"""Path-token extraction and validation for headless Claude session output.

Single manifest load derives three registries: ``_OUTPUT_PATH_TOKENS_BY_SKILL``
(raw per-skill ``file_path*`` output sets), ``_OUTPUT_PATH_TOKENS`` (global
union), and ``_RECOVERABLE_PATH_TOKENS`` (broader union including
``directory_path``). Known skills scope their token candidate set to their own
outputs while unknown and zero-output skills conservatively fall back to the
global set.
"""

from __future__ import annotations

import os
from typing import NewType

import regex as re

from autoskillit.core import get_logger, load_yaml, pkg_root
from autoskillit.execution.session._session_content import _normalize_model_output

logger = get_logger(__name__)
NormalizedMessages = NewType("NormalizedMessages", list[str])


def _normalize_messages(assistant_messages: list[str]) -> NormalizedMessages:
    return NormalizedMessages([_normalize_model_output(msg) for msg in assistant_messages])


_WORKTREE_PATH_PATTERN: re.Pattern[str] = re.compile(r"^worktree_path\s*=\s*(.+)$", re.MULTILINE)
_BRANCH_NAME_PATTERN: re.Pattern[str] = re.compile(r"^branch_name\s*=\s*(.+)$", re.MULTILINE)


def _extract_worktree_path(assistant_messages: NormalizedMessages) -> str | None:
    """Return the last absolute path emitted as worktree_path=<value>."""
    last: str | None = None
    for msg in assistant_messages:
        m = _WORKTREE_PATH_PATTERN.search(msg)
        if m and os.path.isabs(candidate := m.group(1).strip()):
            last = candidate
    return last


def _extract_branch_name(assistant_messages: NormalizedMessages) -> str | None:
    """Return the last branch_name token value emitted."""
    last: str | None = None
    for msg in assistant_messages:
        m = _BRANCH_NAME_PATTERN.search(msg)
        if m and (candidate := m.group(1).strip()):
            last = candidate
    return last


_INTENTIONALLY_EXCLUDED_PATH_TOKENS: frozenset[str] = frozenset({"worktree_path", "branch_name"})


def _build_path_token_registry() -> tuple[
    dict[str, frozenset[str]], frozenset[str], frozenset[str]
]:
    """Single-load derivation of (per-skill, output, recoverable) registries."""
    empty: tuple[dict[str, frozenset[str]], frozenset[str], frozenset[str]] = (
        {},
        frozenset(),
        frozenset(),
    )
    try:
        manifest = load_yaml(pkg_root() / "recipe" / "skill_contracts.yaml")
    except FileNotFoundError:
        logger.debug("skill_contracts.yaml not found; path-token registries will be empty")
        return empty
    except Exception:
        logger.warning("Failed to derive path-token registries from contracts YAML", exc_info=True)
        return empty
    if not isinstance(manifest, dict) or not isinstance(manifest.get("skills"), dict):
        logger.debug("skill_contracts.yaml is empty or non-dict; registries will be empty")
        return empty
    by_skill: dict[str, frozenset[str]] = {}
    output_tokens: set[str] = set()
    recoverable_tokens: set[str] = set()
    for skill_name, skill_data in manifest["skills"].items():
        if not isinstance(skill_data, dict):
            by_skill[skill_name] = frozenset()
            continue
        outputs = skill_data.get("outputs", [])
        if not isinstance(outputs, list):
            by_skill[skill_name] = frozenset()
            continue
        raw_outputs: set[str] = set()
        for out in outputs:
            if not isinstance(out, dict):
                continue
            name = out.get("name", "")
            out_type = out.get("type", "")
            if not isinstance(name, str) or not isinstance(out_type, str):
                continue
            if not name:
                continue
            if out_type.startswith("file_path"):
                raw_outputs.add(name)
                output_tokens.add(name)
                recoverable_tokens.add(name)
            elif out_type == "directory_path":
                recoverable_tokens.add(name)
        by_skill[skill_name] = frozenset(raw_outputs)
    excluded = _INTENTIONALLY_EXCLUDED_PATH_TOKENS
    return (
        by_skill,
        frozenset(output_tokens - excluded),
        frozenset(recoverable_tokens - excluded),
    )


_OUTPUT_PATH_TOKENS_BY_SKILL, _OUTPUT_PATH_TOKENS, _RECOVERABLE_PATH_TOKENS = (
    _build_path_token_registry()
)


def _select_output_path_tokens(skill_name: str | None) -> frozenset[str]:
    """Return token candidate set for the running skill."""
    if not skill_name:
        return _OUTPUT_PATH_TOKENS
    raw = _OUTPUT_PATH_TOKENS_BY_SKILL.get(skill_name)
    if raw is None or not raw:
        return _OUTPUT_PATH_TOKENS
    return raw & _OUTPUT_PATH_TOKENS


_OUTPUT_PATH_PATTERN: re.Pattern[str] = (
    re.compile(
        r"^(" + "|".join(re.escape(t) for t in sorted(_OUTPUT_PATH_TOKENS)) + r")\s*=\s*(.+)$",
        re.MULTILINE,
    )
    if _OUTPUT_PATH_TOKENS
    else re.compile(r"(?!)")
)


def _extract_output_paths(
    assistant_messages: NormalizedMessages,
    *,
    token_scope: frozenset[str],
) -> dict[str, str]:
    """Extract structured output path tokens, filtered against ``token_scope``."""
    paths: dict[str, str] = {}
    for msg in assistant_messages:
        for m in _OUTPUT_PATH_PATTERN.finditer(msg):
            token, value = m.group(1), m.group(2).strip()
            if token not in token_scope:
                continue
            if os.path.isabs(value):
                paths[token] = value
    return paths


def _is_path_outside_cwd(
    path: str,
    cwd: str,
    *,
    allow_relative: bool = False,
) -> bool:
    """Return True iff ``path`` lexically normalizes outside ``cwd``."""
    if not cwd or not os.path.isabs(cwd):
        return False
    norm_cwd = os.path.normpath(cwd)
    if norm_cwd == "/":
        return False
    if not isinstance(path, str) or not path:
        return False
    if not os.path.isabs(path):
        if not allow_relative:
            return False
        path = os.path.join(norm_cwd, path)
    normalized = os.path.normpath(path)
    cwd_prefix = norm_cwd + "/"
    return not normalized.startswith(cwd_prefix) and normalized != norm_cwd


def _validate_output_paths(extracted_paths: dict[str, str], cwd: str) -> str | None:
    """Return a diagnostic string if any path is outside cwd, else None."""
    if not os.path.isabs(cwd) or os.path.normpath(cwd) == "/":
        return None
    violations = [
        f"{token} '{path}' is outside session cwd '{cwd}'"
        for token, path in extracted_paths.items()
        if _is_path_outside_cwd(path, cwd, allow_relative=False)
    ]
    return "; ".join(violations) if violations else None
