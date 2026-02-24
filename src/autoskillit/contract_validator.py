"""Contract-based validation for AutoSkillit pipeline scripts.

Loads a bundled YAML manifest declaring input/output contracts for all 13
skills, resolves skill references in pipeline steps, and validates that
each skill step provides all required inputs.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from autoskillit.skill_resolver import bundled_skills_dir

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SkillInput:
    name: str
    type: str
    required: bool


@dataclasses.dataclass
class SkillOutput:
    name: str
    type: str


@dataclasses.dataclass
class SkillContract:
    inputs: list[SkillInput]
    outputs: list[SkillOutput]


@dataclasses.dataclass
class StaleItem:
    skill: str
    reason: str  # "version_mismatch" | "hash_mismatch"
    stored_value: str
    current_value: str


@dataclasses.dataclass
class DataflowEntry:
    step: str
    available: list[str]
    required: list[str]
    produced: list[str]


@dataclasses.dataclass
class PipelineContract:
    generated_at: str
    bundled_manifest_version: str
    skill_hashes: dict[str, str]
    skills: dict[str, SkillContract]
    dataflow: list[DataflowEntry]


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_SKILL_NAME_RE = re.compile(r"/autoskillit:([\w-]+)")
_CONTEXT_REF_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")
_INPUT_REF_RE = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}")
_TEMPLATE_REF_RE = re.compile(r"\$\{\{[^}]+\}\}")


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def load_bundled_manifest() -> dict[str, Any]:
    """Load the bundled skill_contracts.yaml from the package directory."""
    manifest_path = Path(__file__).parent / "skill_contracts.yaml"
    return yaml.safe_load(manifest_path.read_text())


def resolve_skill_name(skill_command: str) -> str | None:
    """Extract the skill name from a command string.

    Returns the skill name (e.g. "retry-worktree") or None if the command
    does not reference an autoskillit skill or contains dynamic expressions.
    """
    match = _SKILL_NAME_RE.search(skill_command)
    if not match:
        return None
    name = match.group(1)
    # Reject dynamic names containing template expressions
    if "${{" in name:
        return None
    return name


def get_skill_contract(skill_name: str, manifest: dict[str, Any]) -> SkillContract | None:
    """Look up a skill in the manifest and return a SkillContract."""
    skills = manifest.get("skills", {})
    skill_data = skills.get(skill_name)
    if skill_data is None:
        return None
    inputs = [
        SkillInput(
            name=inp["name"],
            type=inp["type"],
            required=inp.get("required", False),
        )
        for inp in skill_data.get("inputs", [])
    ]
    outputs = [
        SkillOutput(name=out["name"], type=out["type"]) for out in skill_data.get("outputs", [])
    ]
    return SkillContract(inputs=inputs, outputs=outputs)


def compute_skill_hash(skill_name: str) -> str:
    """Compute SHA256 hash of a skill's SKILL.md file."""
    skill_md = bundled_skills_dir() / skill_name / "SKILL.md"
    if not skill_md.is_file():
        return ""
    content = skill_md.read_bytes()
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def extract_context_refs(step: Any) -> set[str]:
    """Extract ${{ context.X }} references from a step's with_args."""
    refs: set[str] = set()
    for val in step.with_args.values():
        refs.update(_CONTEXT_REF_RE.findall(str(val)))
    return refs


def extract_input_refs(step: Any) -> set[str]:
    """Extract ${{ inputs.X }} references from a step's with_args."""
    refs: set[str] = set()
    for val in step.with_args.values():
        refs.update(_INPUT_REF_RE.findall(str(val)))
    return refs


def count_positional_args(skill_command: str) -> int:
    """Count positional text tokens after the skill name.

    Tokens that are template references (${{ ... }}) are excluded since
    they are handled by extract_context_refs / extract_input_refs.

    Returns 0 if there are no extra tokens after the skill name.
    """
    match = _SKILL_NAME_RE.search(skill_command)
    if not match:
        return 0
    after_skill = skill_command[match.end() :].strip()
    if not after_skill:
        return 0
    # Remove template references before counting
    without_templates = _TEMPLATE_REF_RE.sub("", after_skill).strip()
    if not without_templates:
        return 0
    return len(without_templates.split())
