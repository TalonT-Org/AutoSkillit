#!/usr/bin/env python3
"""Reject bare dynamic enum construction in registered persisted-format decoders.

This script duplicates the decoder/enum registry from ``PERSISTED_FORMAT_LEDGER``
because pre-commit guards must run without importing the package. Contract tests keep
the two registries synchronized.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autoskillit"

PERSISTED_ENUM_DECODERS: Mapping[str, frozenset[str]] = {
    "core/_retiring_cache.py": frozenset({"PluginArtifactKind"}),
    "fleet/state_records.py": frozenset({"DispatchStatus"}),
    "hooks/_capture/_ledger.py": frozenset(
        {
            "CaptureDeliveryStatus",
            "CaptureReferenceStatus",
            "CaptureRetentionPhase",
            "CaptureSnapshotStatus",
            "CaptureState",
            "CaptureStatus",
        }
    ),
    "execution/session/_skill_session_contract_codec.py": frozenset(
        {
            "ExplorationVectorApplicabilityId",
            "ExplorationVectorDisposition",
            "RelationshipKind",
            "RepositoryProfileId",
        }
    ),
}

# These functions decode at the same boundary that quarantines the containing
# record/frame. Keeping this allowlist exact makes each exception reviewable.
_QUARANTINE_CONSTRUCTOR_ALLOWLIST: Mapping[tuple[str, str, str], str] = {
    (
        "core/_retiring_cache.py",
        "_record_from_json",
        "PluginArtifactKind",
    ): "the caller quarantines the complete retiring-cache record",
    (
        "core/_retiring_cache.py",
        "_legacy_from_json",
        "PluginArtifactKind",
    ): "the caller quarantines the complete legacy-evidence record",
    (
        "hooks/_capture/_ledger.py",
        "record_from_dict",
        "CaptureState",
    ): "the ledger view quarantines the complete framed lifecycle record",
    (
        "hooks/_capture/_ledger.py",
        "record_from_dict",
        "CaptureStatus",
    ): "the ledger view quarantines the complete framed lifecycle record",
    (
        "hooks/_capture/_ledger.py",
        "record_from_dict",
        "CaptureSnapshotStatus",
    ): "the ledger view quarantines the complete framed lifecycle record",
    (
        "hooks/_capture/_ledger.py",
        "record_from_dict",
        "CaptureReferenceStatus",
    ): "the ledger view quarantines the complete framed lifecycle record",
    (
        "hooks/_capture/_ledger.py",
        "record_from_dict",
        "CaptureDeliveryStatus",
    ): "the ledger view quarantines the complete framed lifecycle record",
    (
        "hooks/_capture/_ledger.py",
        "record_from_dict",
        "CaptureRetentionPhase",
    ): "the ledger view quarantines the complete framed lifecycle record",
    (
        "hooks/_capture/_ledger.py",
        "legacy_record_from_dict",
        "CaptureState",
    ): "the ledger view quarantines the complete framed legacy record",
}

_TOLERANT_METHODS = frozenset({"from_persisted"})
_TOLERANT_HELPERS = frozenset({"_persisted_enum"})


class _EnumResolver:
    """Resolve imported and simple local aliases back to registered enum names."""

    def __init__(self, tree: ast.Module, enum_names: frozenset[str]) -> None:
        self._enum_names = enum_names
        self._aliases: dict[str, str] = {name: name for name in enum_names}
        self._module_aliases: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in enum_names:
                        self._aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self._module_aliases.add(alias.asname or alias.name.split(".")[0])

        # Resolve simple aliases such as ``Status = DispatchStatus``. Iterate so a
        # short alias chain cannot evade the guard.
        changed = True
        while changed:
            changed = False
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                resolved = self.resolve_expr(value) if value is not None else None
                if resolved is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in self._aliases:
                        self._aliases[target.id] = resolved
                        changed = True

    def resolve_expr(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self._aliases.get(node.id)
        if isinstance(node, ast.Attribute) and node.attr in self._enum_names:
            module_root = node.value
            while isinstance(module_root, ast.Attribute):
                module_root = module_root.value
            if isinstance(module_root, ast.Name) and module_root.id in self._module_aliases:
                return node.attr
        return None


def _build_parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _enclosing_function(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> str | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return None


def _is_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_literal(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None and _is_literal(key) and _is_literal(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_literal(node.operand)
    return False


def _referenced_enum(call: ast.Call, resolver: _EnumResolver) -> tuple[str, bool] | None:
    """Return ``(enum_name, is_direct_constructor)`` for a recognized call."""
    direct = resolver.resolve_expr(call.func)
    if direct is not None:
        return direct, True

    if isinstance(call.func, ast.Attribute) and call.func.attr in _TOLERANT_METHODS:
        enum_name = resolver.resolve_expr(call.func.value)
        if enum_name is not None:
            return enum_name, False

    if isinstance(call.func, ast.Name) and call.func.id in _TOLERANT_HELPERS and call.args:
        enum_name = resolver.resolve_expr(call.args[0])
        if enum_name is not None:
            return enum_name, False
    return None


def discover_persisted_enum_references(
    src_root: Path = SRC_ROOT,
) -> set[tuple[str, str]]:
    """Return ``(decoder_module, enum_name)`` pairs referenced by decoder calls."""
    references: set[tuple[str, str]] = set()
    for module, enum_names in PERSISTED_ENUM_DECODERS.items():
        path = src_root / module
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        resolver = _EnumResolver(tree, enum_names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            referenced = _referenced_enum(node, resolver)
            if referenced is not None:
                references.add((module, referenced[0]))
    return references


def find_bare_enum_constructions(src_root: Path = SRC_ROOT) -> list[str]:
    """Return dynamic direct enum constructions outside named quarantine paths."""
    violations: list[str] = []
    for module, enum_names in PERSISTED_ENUM_DECODERS.items():
        path = src_root / module
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        resolver = _EnumResolver(tree, enum_names)
        parents = _build_parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            referenced = _referenced_enum(node, resolver)
            if referenced is None or not referenced[1]:
                continue
            enum_name = referenced[0]
            if (
                node.args
                and all(_is_literal(arg) for arg in node.args)
                and all(_is_literal(keyword.value) for keyword in node.keywords)
            ):
                continue
            function_name = _enclosing_function(node, parents)
            if (
                function_name is not None
                and (
                    module,
                    function_name,
                    enum_name,
                )
                in _QUARANTINE_CONSTRUCTOR_ALLOWLIST
            ):
                continue
            violations.append(
                f"{module}:{node.lineno}: bare dynamic construction of {enum_name}; "
                "use its tolerant constructor or quarantine the containing record"
            )
    return sorted(violations)


def find_missing_registered_modules(src_root: Path = SRC_ROOT) -> list[str]:
    """Return registered decoder modules that do not exist under *src_root*."""
    return [
        f"{module}: registered persisted-format decoder module does not exist"
        for module in PERSISTED_ENUM_DECODERS
        if not (src_root / module).is_file()
    ]


def check(src_root: Path = SRC_ROOT) -> list[str]:
    """Return every persisted-enum decoding violation under *src_root*."""
    return [
        *find_missing_registered_modules(src_root),
        *find_bare_enum_constructions(src_root),
    ]


def main() -> int:
    violations = check()
    if violations:
        print("Persisted enum decoding violations found:\n")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("Every registered persisted enum uses a tolerant or quarantined decode path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
