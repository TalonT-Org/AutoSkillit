"""Skill exploration-vector sidecar parsing and HTML-marker binding.

Combines the exploration-sidecar YAML parsing with the HTML-marker binding into
one cohesive shard because they share state (the sidecar loader produces the
vector dict that the marker binder rewrites). Both are listed as a single
concern in the decompose-skills issue.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import regex as re

from autoskillit.core import (
    ExplorationTaskSpec,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    RelationshipKind,
    RepositoryProfileId,
    SkillContractError,
    load_yaml,
)
from autoskillit.workspace.skill_format import _normalize_exploration_vector_body

_SIDECAR_MIGRATED_KEYS = frozenset(
    {"id", "role", "relationship_classes", "rationale", "applicability"}
)
_SIDECAR_RETAINED_KEYS = frozenset({"id", "rationale"})
_SIDECAR_FILENAME = "exploration.yaml"
_EXPLORATION_VECTOR_MARKER_TOKEN = "autoskillit:exploration-vector"
_EXPLORATION_VECTOR_OPEN_RE = re.compile(
    r'^<!-- autoskillit:exploration-vector id="'
    r'(?P<id>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)" -->$'
)
_EXPLORATION_VECTOR_CLOSE = "<!-- /autoskillit:exploration-vector -->"
_VECTOR_DEFAULT_PROFILE = RepositoryProfileId.AUTO
_VECTOR_DEFAULT_DEPENDS_ON: tuple[str, ...] = ()
_VECTOR_DEFAULT_SCOPE: tuple[str, ...] = (".",)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SkillContractError(f"exploration vector {field_name} must be a list of strings")
    return tuple(value)


def _load_exploration_sidecar(skill_md_path: Path) -> tuple[object | None, str]:
    """Load exploration.yaml sidecar and return (parsed_yaml, sha256_hex).

    Returns (None, "") if the sidecar does not exist.
    """
    sidecar_path = skill_md_path.parent / _SIDECAR_FILENAME
    try:
        raw_bytes = sidecar_path.read_bytes()
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        raise SkillContractError(f"cannot read exploration sidecar {sidecar_path}: {exc}") from exc
    sidecar_digest = hashlib.sha256(raw_bytes).hexdigest()
    try:
        parsed = load_yaml(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise SkillContractError(
            f"exploration sidecar {sidecar_path} is not valid YAML: {exc}"
        ) from exc
    return parsed, sidecar_digest


def _parse_exploration_sidecar(
    data: object,
    skill_name: str,
) -> tuple[ExplorationVectorDef, ...]:
    """Parse the slim exploration.yaml schema into typed ExplorationVectorDef tuples."""
    if data is None:
        return ()
    if not isinstance(data, dict):
        raise SkillContractError("exploration sidecar must be a YAML mapping")
    allowed_top_keys = {"vectors", "retained"}
    if set(data) - allowed_top_keys:
        raise SkillContractError(
            f"exploration sidecar contains unknown top-level keys: "
            f"{sorted(set(data) - allowed_top_keys)!r}"
        )
    vectors: list[ExplorationVectorDef] = []

    for index, item in enumerate(data.get("vectors") or []):
        if not isinstance(item, dict):
            raise SkillContractError(f"exploration sidecar vectors[{index}] must be a mapping")
        unknown = set(item) - _SIDECAR_MIGRATED_KEYS
        if unknown:
            raise SkillContractError(
                f"exploration sidecar vectors[{index}] contains unknown keys: {sorted(unknown)!r}"
            )
        try:
            for field_name in ("id", "role", "rationale"):
                if not isinstance(item[field_name], str):
                    raise SkillContractError(
                        f"exploration sidecar vectors[{index}].{field_name} must be text"
                    )
            applicability_raw = item.get("applicability", "always")
            if not isinstance(applicability_raw, str):
                raise SkillContractError(
                    f"exploration sidecar vectors[{index}].applicability must be text"
                )
            vector_id = item["id"]
            # Derive task_id and frontier_item_id from skill_name + vector id
            task_id = f"{skill_name}-{vector_id}"
            frontier_item_id = f"{task_id}-frontier"
            profile = _VECTOR_DEFAULT_PROFILE
            try:
                applicability = ExplorationVectorApplicabilityId(applicability_raw)
            except ValueError as exc:
                raise SkillContractError(
                    f"exploration sidecar vectors[{index}].applicability={applicability_raw!r} "
                    f"is not a valid applicability id"
                ) from exc
            try:
                relationship_classes = tuple(
                    RelationshipKind(relationship)
                    for relationship in _string_tuple(
                        item["relationship_classes"],
                        "relationship_classes",
                    )
                )
            except ValueError as exc:
                raise SkillContractError(
                    f"exploration sidecar vectors[{index}].relationship_classes contains an "
                    f"invalid entry: {exc}"
                ) from exc
            vector = ExplorationVectorDef(
                id=vector_id,
                disposition=ExplorationVectorDisposition.MIGRATED,
                rationale=item["rationale"],
                applicability=applicability,
                role=item["role"],
                profile=profile,
                relationship_classes=relationship_classes,
                task=ExplorationTaskSpec(
                    task_id=task_id,
                    frontier_item_id=frontier_item_id,
                    profile=profile,
                    depends_on=_VECTOR_DEFAULT_DEPENDS_ON,
                    scope=_VECTOR_DEFAULT_SCOPE,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SkillContractError(
                f"exploration sidecar vectors[{index}] contains an invalid value: {exc}"
            ) from exc
        vectors.append(vector)

    for index, item in enumerate(data.get("retained") or []):
        if not isinstance(item, dict):
            raise SkillContractError(f"exploration sidecar retained[{index}] must be a mapping")
        unknown = set(item) - _SIDECAR_RETAINED_KEYS
        if unknown:
            raise SkillContractError(
                f"exploration sidecar retained[{index}] contains unknown keys: {sorted(unknown)!r}"
            )
        for field_name in ("id", "rationale"):
            value = item.get(field_name)
            if not isinstance(value, str):
                raise SkillContractError(
                    f"exploration sidecar retained[{index}].{field_name} must be text"
                )
        vector_id = item["id"]
        task_id = f"{skill_name}-{vector_id}"
        frontier_item_id = f"{task_id}-frontier"
        profile = _VECTOR_DEFAULT_PROFILE
        vector = ExplorationVectorDef(
            id=vector_id,
            disposition=ExplorationVectorDisposition.RETAINED,
            rationale=item["rationale"],
            applicability=ExplorationVectorApplicabilityId.ALWAYS,
            role=None,
            profile=profile,
            relationship_classes=(RelationshipKind.REFERENCES,),
            task=ExplorationTaskSpec(
                task_id=task_id,
                frontier_item_id=frontier_item_id,
                profile=profile,
                depends_on=_VECTOR_DEFAULT_DEPENDS_ON,
                scope=_VECTOR_DEFAULT_SCOPE,
            ),
        )
        vectors.append(vector)

    ids = tuple(vector.id for vector in vectors)
    if len(ids) != len(set(ids)):
        raise SkillContractError("exploration sidecar vector ids must be unique")
    return tuple(vectors)


def _bind_exploration_vector_markers(
    content: str,
    vectors: tuple[ExplorationVectorDef, ...],
) -> tuple[ExplorationVectorDef, ...]:
    declared = {vector.id: vector for vector in vectors}
    if len(declared) != len(vectors):
        raise SkillContractError("exploration vector ids must be unique")
    bodies: dict[str, str] = {}
    active_id: str | None = None
    body_lines: list[str] = []
    for raw_line in content.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if _EXPLORATION_VECTOR_MARKER_TOKEN not in line:
            if active_id is not None:
                body_lines.append(raw_line)
            continue
        opening = _EXPLORATION_VECTOR_OPEN_RE.fullmatch(line)
        if opening is not None:
            marker_id = opening.group("id")
            if active_id is not None:
                raise SkillContractError("exploration vector markers cannot be nested")
            if marker_id not in declared:
                raise SkillContractError(f"unknown exploration vector marker {marker_id!r}")
            if marker_id in bodies:
                raise SkillContractError(f"duplicate exploration vector marker {marker_id!r}")
            active_id = marker_id
            body_lines = []
            continue
        if line == _EXPLORATION_VECTOR_CLOSE:
            if active_id is None:
                raise SkillContractError("mismatched exploration vector closing marker")
            body = _normalize_exploration_vector_body("".join(body_lines))
            if not body.strip():
                raise SkillContractError(f"exploration vector {active_id!r} has an empty body")
            bodies[active_id] = body
            active_id = None
            body_lines = []
            continue
        raise SkillContractError("malformed or embedded exploration vector marker token")
    if active_id is not None:
        raise SkillContractError(f"exploration vector {active_id!r} is missing its closing marker")
    missing = set(declared).difference(bodies)
    if missing:
        raise SkillContractError(f"missing exploration vector markers: {sorted(missing)!r}")
    return tuple(replace(vector, body=bodies[vector.id]) for vector in vectors)


def replace_exploration_vector_bodies(
    content: str,
    vectors: tuple[ExplorationVectorDef, ...],
    replacements: Mapping[str, str],
) -> str:
    """Replace exactly every migrated marker body while retaining reviewed prose."""
    bound = _bind_exploration_vector_markers(content, vectors)
    supplied = {vector.id: vector for vector in vectors}
    if any(vector.body != supplied[vector.id].body for vector in bound):
        raise SkillContractError(
            "exploration vector body differs from its canonical parsed authority"
        )
    expected = {
        vector.id
        for vector in bound
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
    }
    supplied_ids = set(replacements)
    if supplied_ids != expected:
        missing = sorted(expected - supplied_ids)
        extra = sorted(supplied_ids - expected)
        raise SkillContractError(
            "exploration vector replacements must exactly match migrated marker ids: "
            f"missing ({len(missing)})={missing!r}, extra ({len(extra)})={extra!r}"
        )
    normalized: dict[str, str] = {}
    for marker_id, replacement_body in replacements.items():
        if not isinstance(replacement_body, str) or not replacement_body.strip():
            raise SkillContractError(f"replacement for exploration vector {marker_id!r} is empty")
        replacement_body = _normalize_exploration_vector_body(replacement_body)
        if _EXPLORATION_VECTOR_MARKER_TOKEN in replacement_body:
            raise SkillContractError("exploration vector replacement contains a marker token")
        normalized[marker_id] = replacement_body

    output: list[str] = []
    active_id: str | None = None
    for raw_line in content.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        opening = _EXPLORATION_VECTOR_OPEN_RE.fullmatch(line)
        if opening is not None:
            active_id = opening.group("id")
            output.append(raw_line)
            if active_id in normalized:
                output.append(normalized[active_id] + "\n")
            continue
        if line == _EXPLORATION_VECTOR_CLOSE:
            output.append(raw_line)
            active_id = None
            continue
        if active_id not in normalized:
            output.append(raw_line)
    return "".join(output)


__all__ = [
    "_bind_exploration_vector_markers",
    "_load_exploration_sidecar",
    "_parse_exploration_sidecar",
    "replace_exploration_vector_bodies",
]
