#!/usr/bin/env python3
"""Resolve the runner and test-filter policy for a GitHub Actions event."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

_HEAD_REF_PREFIX = "refs/heads/"
_COMMIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class CiTargetPolicyDef:
    """Static CI behavior registered for one target branch."""

    os_runners: tuple[str, ...]
    filter_mode: str


CI_TARGET_POLICIES: Mapping[str, CiTargetPolicyDef] = MappingProxyType(
    {
        "develop": CiTargetPolicyDef(("ubuntu-latest",), "conservative"),
        "main": CiTargetPolicyDef(("ubuntu-latest",), "none"),
        "stable": CiTargetPolicyDef(("ubuntu-latest", "macos-15"), "none"),
    }
)

ALLOWED_TARGETS_BY_EVENT: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "pull_request": frozenset({"develop", "main", "stable"}),
        "merge_group": frozenset({"develop", "main", "stable"}),
        "push": frozenset({"main", "stable"}),
    }
)


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _require_string(mapping: Mapping[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _target_from_head_ref(ref: str, field: str) -> str:
    if not ref.startswith(_HEAD_REF_PREFIX):
        raise ValueError(f"{field} must start with {_HEAD_REF_PREFIX!r}")
    target = ref.removeprefix(_HEAD_REF_PREFIX)
    if not target:
        raise ValueError(f"{field} must name a branch")
    return target


def _require_commit_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _COMMIT_SHA_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a 40-character lowercase commit SHA")
    return value


def resolve_ci_profile(
    event_name: str,
    payload: Mapping[str, object],
) -> tuple[CiTargetPolicyDef, str]:
    """Resolve the immutable target policy and conservative-filter base revision."""

    allowed_targets = ALLOWED_TARGETS_BY_EVENT.get(event_name)
    if allowed_targets is None:
        raise ValueError(f"unsupported event: {event_name!r}")

    event_payload = _require_mapping(payload, "event payload")
    base_sha: object | None = None
    if event_name == "pull_request":
        pull_request = _require_mapping(
            event_payload.get("pull_request"),
            "pull_request",
        )
        base = _require_mapping(pull_request.get("base"), "pull_request.base")
        target = _require_string(base, "ref")
        base_sha = base.get("sha")
    elif event_name == "merge_group":
        merge_group = _require_mapping(
            event_payload.get("merge_group"),
            "merge_group",
        )
        target = _target_from_head_ref(
            _require_string(merge_group, "base_ref"),
            "merge_group.base_ref",
        )
        base_sha = merge_group.get("base_sha")
    else:
        target = _target_from_head_ref(
            _require_string(event_payload, "ref"),
            "ref",
        )

    if target not in allowed_targets:
        raise ValueError(f"target {target!r} is not allowed for event {event_name!r}")
    policy = CI_TARGET_POLICIES[target]

    base_revision = ""
    if policy.filter_mode == "conservative":
        base_revision = _require_commit_sha(base_sha, "base SHA")
    return policy, base_revision


def _load_event() -> tuple[str, Mapping[str, object]]:
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_name:
        raise ValueError("GITHUB_EVENT_NAME is required")
    if not event_path:
        raise ValueError("GITHUB_EVENT_PATH is required")

    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub event payload must be an object")
    return event_name, payload


def main() -> int:
    """Write a complete GitHub Actions output record, or fail without stdout."""

    try:
        event_name, payload = _load_event()
        policy, base_revision = resolve_ci_profile(event_name, payload)
        output_lines = (
            "os-matrix=" + json.dumps(list(policy.os_runners), separators=(",", ":")),
            f"test-filter-mode={policy.filter_mode}",
            f"test-base-revision={base_revision}",
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"ci_target_policy: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write("\n".join(output_lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
