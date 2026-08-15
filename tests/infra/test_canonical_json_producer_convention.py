"""Producer ratchet: enforce that every strict-canonical JSON consumer has a
registered, verified server-side producer.

Scans src/autoskillit/ for decode_versioned_json_bytes(..., require_canonical=True)
call sites. Each such site must be registered in _CANONICAL_JSON_ARTIFACT_REGISTRY,
pointing at a producer function that either calls write_canonical_versioned_json or
exclusively writes already-canonical bytes, and at any child-facing SKILL.md section
that names the producer's MCP tool by symbol. This closes the
gap #4406 exhibited: a Python consumer demanding canonical bytes with no mechanical
guarantee that its LLM-agent producer emits them.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


class CanonicalArtifactDef(NamedTuple):
    consumer_site: tuple[str, int]
    producer_symbol: str
    producer_path: str
    producer_function: str
    skill_md_refs: tuple[tuple[str, int, int], ...]


def _is_literal_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _scan_require_canonical_consumer_sites() -> set[tuple[str, int]]:
    """AST-scan src/autoskillit/ for decode_versioned_json_bytes(require_canonical=True).

    Returns set of (relative_path, line_number) for call sites that pass
    require_canonical=True as a keyword argument.
    """
    src_root = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
    sites: set[tuple[str, int]] = set()

    for py_file in src_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_decode_call = (
                isinstance(func, ast.Name) and func.id == "decode_versioned_json_bytes"
            ) or (isinstance(func, ast.Attribute) and func.attr == "decode_versioned_json_bytes")
            if not is_decode_call:
                continue
            for kw in node.keywords:
                if kw.arg == "require_canonical" and _is_literal_true(kw.value):
                    rel = str(py_file.relative_to(src_root.parent.parent))
                    sites.add((rel, node.lineno))
                    break

    return sites


def _find_function_by_name(
    tree: ast.Module, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _find_call_at_line(tree: ast.Module, lineno: int, func_name: str) -> ast.Call | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.lineno != lineno:
            continue
        func = node.func
        matches = (isinstance(func, ast.Name) and func.id == func_name) or (
            isinstance(func, ast.Attribute) and func.attr == func_name
        )
        if matches:
            return node
    return None


def _scan_child_tool_authority_producer_sites() -> set[tuple[str, int, str]]:
    """Find authority construction or canonicalization in child-facing MCP tools."""
    repo_root = Path(__file__).resolve().parents[2]
    tools_root = repo_root / "src" / "autoskillit" / "server" / "tools"
    sites: set[tuple[str, int, str]] = set()
    for source_path in tools_root.glob("tools_*.py"):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        relative_path = str(source_path.relative_to(repo_root))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Name) and func.id == "AuditCycleAuthority") or (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "AuditCycleAuthority"
                    and func.attr in {"create", "from_dict"}
                ):
                    sites.add((relative_path, node.lineno, "construction"))
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "canonical_bytes"
                and isinstance(node.value, ast.Name)
                and "authority" in node.value.id.lower()
            ):
                sites.add((relative_path, node.lineno, "canonicalization"))
    return sites


_MATERIALIZER_PRODUCER_PATH = "src/autoskillit/server/_audit_authority_materializer.py"
_TYPED_PRODUCER_MODULE = "src/autoskillit/server/tools/tools_audit_artifacts.py"

_CANONICAL_JSON_ARTIFACT_REGISTRY: dict[str, CanonicalArtifactDef] = {
    "authority": CanonicalArtifactDef(
        consumer_site=("src/autoskillit/core/audit_cycle_verifier.py", 427),
        producer_symbol="_write_or_verify",
        producer_path=_MATERIALIZER_PRODUCER_PATH,
        producer_function="_write_or_verify",
        skill_md_refs=(),
    ),
    "disposition_report": CanonicalArtifactDef(
        consumer_site=("src/autoskillit/core/audit_cycle_verifier.py", 447),
        producer_symbol="write_audit_disposition_bundle",
        producer_path=_TYPED_PRODUCER_MODULE,
        producer_function="_write_disposition_report",
        skill_md_refs=(("src/autoskillit/skills_extended/make-plan/SKILL.md", 369, 381),),
    ),
    "inventory": CanonicalArtifactDef(
        consumer_site=("src/autoskillit/core/audit_cycle_verifier.py", 575),
        producer_symbol="_write_or_verify",
        producer_path=_MATERIALIZER_PRODUCER_PATH,
        producer_function="_write_or_verify",
        skill_md_refs=(),
    ),
    "plan_association": CanonicalArtifactDef(
        consumer_site=("src/autoskillit/recipe/_cmd_rpc_guards.py", 280),
        producer_symbol="write_audit_disposition_bundle",
        producer_path=_TYPED_PRODUCER_MODULE,
        producer_function="_write_plan_association",
        skill_md_refs=(("src/autoskillit/skills_extended/make-plan/SKILL.md", 369, 381),),
    ),
    "audit_semantic_result": CanonicalArtifactDef(
        consumer_site=("src/autoskillit/core/audit_semantic_codec.py", 229),
        producer_symbol="write_audit_semantic_result",
        producer_path=_TYPED_PRODUCER_MODULE,
        producer_function="_write_semantic_result",
        skill_md_refs=(("src/autoskillit/skills_extended/audit-impl/SKILL.md", 98, 108),),
    ),
    "standalone_audit_evidence": CanonicalArtifactDef(
        consumer_site=("src/autoskillit/core/audit_semantic_codec.py", 276),
        producer_symbol="write_standalone_audit_evidence",
        producer_path=_TYPED_PRODUCER_MODULE,
        producer_function="_write_standalone_evidence",
        skill_md_refs=(("src/autoskillit/skills_extended/audit-impl/SKILL.md", 109, 118),),
    ),
}


# Documented exception: consumer sites that intentionally decode non-canonical
# versioned JSON. Paired with the writer that produces them so a future accidental
# flip to require_canonical=True is caught by test_non_canonical_exceptions_below.
_NON_CANONICAL_JSON_EXCEPTIONS: dict[str, tuple[tuple[str, int], str]] = {
    "closure_report.json": (
        ("src/autoskillit/core/closure_verifier.py", 59),
        "Closure reports are written by write_versioned_json (see the "
        "_write_report fixture in tests/core/test_closure_verifier.py), not "
        "write_canonical_versioned_json — no content-addressed tamper-evidence "
        "chain requires byte-exact canonical bytes for this artifact.",
    ),
}


class TestCanonicalJsonProducerConvention:
    def test_authority_has_one_materializer_owned_producer(self):
        authority = _CANONICAL_JSON_ARTIFACT_REGISTRY["authority"]

        assert authority.producer_symbol == "_write_or_verify"
        assert authority.producer_path == _MATERIALIZER_PRODUCER_PATH
        assert _scan_child_tool_authority_producer_sites() == set()

    def test_require_canonical_consumers_have_registered_producers(self):
        """Every require_canonical=True consumer site must have a registry entry."""
        current = _scan_require_canonical_consumer_sites()
        registered = {entry.consumer_site for entry in _CANONICAL_JSON_ARTIFACT_REGISTRY.values()}
        added = current - registered
        removed = registered - current

        msg_parts = []
        if added:
            msg_parts.append(
                "New require_canonical=True consumer sites found (register a "
                "CanonicalArtifactDef in _CANONICAL_JSON_ARTIFACT_REGISTRY):\n"
                + "\n".join(f"  + {f}:{ln}" for f, ln in sorted(added))
            )
        if removed:
            msg_parts.append(
                "Registered consumer sites no longer found (remove from "
                "_CANONICAL_JSON_ARTIFACT_REGISTRY):\n"
                + "\n".join(f"  - {f}:{ln}" for f, ln in sorted(removed))
            )
        assert current == registered, "\n\n".join(msg_parts)

    def test_registered_producers_are_sanctioned_canonical_writers(self):
        """Every registered producer writes canonical objects or prepared canonical bytes."""
        repo_root = Path(__file__).resolve().parents[2]
        checked: set[tuple[str, str]] = set()
        for kind, entry in _CANONICAL_JSON_ARTIFACT_REGISTRY.items():
            producer = (entry.producer_path, entry.producer_function)
            if producer in checked:
                continue
            checked.add(producer)
            source_path = repo_root / entry.producer_path
            tree = ast.parse(source_path.read_text(), filename=str(source_path))
            function = _find_function_by_name(tree, entry.producer_function)
            assert function is not None, (
                f"{kind}: no function named {entry.producer_function} in {entry.producer_path}"
            )
            call_names = {
                node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                for node in ast.walk(function)
                if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
            }
            assert {
                "write_canonical_versioned_json",
                "atomic_write",
            } & call_names, (
                f"{kind}: producer {entry.producer_path}:{entry.producer_function} "
                "no longer calls "
                "a sanctioned canonical writer"
            )

    def test_registered_skill_md_refs_name_the_producer(self):
        """Every registered skill_md_ref section must mention the producer's symbol."""
        repo_root = Path(__file__).resolve().parents[2]
        for kind, entry in _CANONICAL_JSON_ARTIFACT_REGISTRY.items():
            for relative_path, start_line, end_line in entry.skill_md_refs:
                skill_md_path = repo_root / relative_path
                lines = skill_md_path.read_text().splitlines()
                section = "\n".join(lines[start_line - 1 : end_line])
                assert entry.producer_symbol in section, (
                    f"{kind}: {relative_path}:{start_line}-{end_line} does not mention "
                    f"{entry.producer_symbol!r}"
                )

    def test_new_require_canonical_consumer_without_registered_producer_fails(self, monkeypatch):
        """Meta-test: a fake extra consumer site should cause the ratchet to fail."""
        original_scan = _scan_require_canonical_consumer_sites

        def patched_scan():
            sites = original_scan()
            sites.add(("src/autoskillit/fake_canonical_module.py", 999))
            return sites

        monkeypatch.setattr(
            "tests.infra.test_canonical_json_producer_convention."
            "_scan_require_canonical_consumer_sites",
            patched_scan,
        )
        with pytest.raises(AssertionError, match="fake_canonical_module"):
            self.test_require_canonical_consumers_have_registered_producers()

    def test_non_canonical_exceptions_still_resolve_to_non_canonical_reads(self):
        """Documented non-canonical exceptions must not pass require_canonical=True."""
        repo_root = Path(__file__).resolve().parents[2]
        for artifact_name, (site, reason) in _NON_CANONICAL_JSON_EXCEPTIONS.items():
            assert reason, f"{artifact_name} has an empty exception reason"
            relative_path, lineno = site
            source_path = repo_root / relative_path
            tree = ast.parse(source_path.read_text(), filename=str(source_path))
            call = _find_call_at_line(tree, lineno, "decode_versioned_json_bytes")
            assert call is not None, (
                f"{artifact_name}: no decode_versioned_json_bytes call found at "
                f"{relative_path}:{lineno}"
            )
            for kw in call.keywords:
                if kw.arg == "require_canonical":
                    assert not _is_literal_true(kw.value), (
                        f"{artifact_name}: {relative_path}:{lineno} now passes "
                        "require_canonical=True — this pairing is documented as "
                        "non-canonical in _NON_CANONICAL_JSON_EXCEPTIONS; either "
                        "register it in _CANONICAL_JSON_ARTIFACT_REGISTRY instead or "
                        "revert the flag"
                    )
