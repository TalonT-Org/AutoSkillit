"""Ratchet strict canonical JSON consumers to their registered producers.

Consumer identity is the source path plus its dotted enclosing definition scope,
independent of line numbers. A decode call is strict when require_canonical is true,
not a literal false, or supplied through **kwargs. Each strict consumer scope must be
registered in _CANONICAL_JSON_ARTIFACT_REGISTRY, pointing at a producer function that
either calls write_canonical_versioned_json or exclusively writes already-canonical
bytes, and at any child-facing SKILL.md section that names the producer's MCP tool by
symbol. This closes the gap #4406 exhibited: a Python consumer demanding canonical
bytes with no mechanical guarantee that its LLM-agent producer emits them.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest

from tests._ast_helpers import MODULE_SCOPE, call_name, count_differences, scope_qualnames

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


class CanonicalArtifactDef(NamedTuple):
    consumer_path: str
    consumer_function: str
    producer_symbol: str
    producer_path: str
    producer_function: str
    skill_md_refs: tuple[tuple[str, int, int], ...]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"


def _is_strict(call: ast.Call) -> bool:
    """Return whether a decode call may require canonical bytes."""
    if any(keyword.arg is None for keyword in call.keywords):
        return True
    for keyword in call.keywords:
        if keyword.arg == "require_canonical":
            return not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
    return False


def _decode_call_sites(tree: ast.AST, relpath: str) -> Counter[tuple[str, str, bool]]:
    """Count decode calls by relative path, enclosing scope, and strictness."""
    scopes = scope_qualnames(tree)
    sites: Counter[tuple[str, str, bool]] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == "decode_versioned_json_bytes":
            sites[(relpath, scopes[node], _is_strict(node))] += 1
    return sites


def _scan_decode_call_sites() -> Counter[tuple[str, str, bool]]:
    """Scan source decode calls; an unparseable file fails here with its filename.

    This check is independent of lint and typecheck and does not rely on their presence
    or execution order.
    """
    sites: Counter[tuple[str, str, bool]] = Counter()
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_path = str(path.relative_to(_REPO_ROOT))
        sites.update(_decode_call_sites(tree, relative_path))
    return sites


def _find_function_by_name(
    tree: ast.Module, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
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
        consumer_path="src/autoskillit/core/audit/audit_cycle_verifier.py",
        consumer_function="AuditCycleVerifier.decode_authority",
        producer_symbol="_write_or_verify",
        producer_path=_MATERIALIZER_PRODUCER_PATH,
        producer_function="_write_or_verify",
        skill_md_refs=(),
    ),
    "disposition_report": CanonicalArtifactDef(
        consumer_path="src/autoskillit/core/audit/audit_cycle_verifier.py",
        consumer_function="AuditCycleVerifier.load_report",
        producer_symbol="write_audit_disposition_bundle",
        producer_path=_TYPED_PRODUCER_MODULE,
        producer_function="_write_disposition_report",
        skill_md_refs=(("src/autoskillit/skills_extended/make-plan/SKILL.md", 386, 398),),
    ),
    "inventory": CanonicalArtifactDef(
        consumer_path="src/autoskillit/core/audit/audit_cycle_verifier.py",
        consumer_function="AuditCycleVerifier._decode_inventory_requirement_ids",
        producer_symbol="_write_or_verify",
        producer_path=_MATERIALIZER_PRODUCER_PATH,
        producer_function="_write_or_verify",
        skill_md_refs=(),
    ),
    "plan_association": CanonicalArtifactDef(
        consumer_path="src/autoskillit/recipe/cmd_rpc/_cmd_rpc_guards.py",
        consumer_function="_load_associated_disposition",
        producer_symbol="write_audit_disposition_bundle",
        producer_path=_TYPED_PRODUCER_MODULE,
        producer_function="_write_plan_association",
        skill_md_refs=(("src/autoskillit/skills_extended/make-plan/SKILL.md", 386, 398),),
    ),
    "audit_semantic_result": CanonicalArtifactDef(
        consumer_path="src/autoskillit/core/audit/audit_semantic_codec.py",
        consumer_function="load_audit_semantic_result",
        producer_symbol="write_audit_semantic_result",
        producer_path=_TYPED_PRODUCER_MODULE,
        producer_function="_write_semantic_result",
        skill_md_refs=(("src/autoskillit/skills_extended/audit-impl/SKILL.md", 103, 117),),
    ),
    "standalone_audit_evidence": CanonicalArtifactDef(
        consumer_path="src/autoskillit/core/audit/audit_semantic_codec.py",
        consumer_function="load_standalone_audit_evidence",
        producer_symbol="write_standalone_audit_evidence",
        producer_path=_TYPED_PRODUCER_MODULE,
        producer_function="_write_standalone_evidence",
        skill_md_refs=(("src/autoskillit/skills_extended/audit-impl/SKILL.md", 118, 127),),
    ),
}


# Documented exception: scopes that intentionally decode non-canonical versioned JSON.
# Their reasons cover every non-canonical read in the scope.
_NON_CANONICAL_JSON_EXCEPTIONS: dict[str, tuple[tuple[str, str], str]] = {
    "closure_report.json": (
        (
            "src/autoskillit/core/audit/closure_verifier.py",
            "verify_closure_report",
        ),
        "Closure reports are written by write_versioned_json (see the "
        "_write_report fixture in tests/core/test_closure_verifier.py), not "
        "write_canonical_versioned_json — no content-addressed tamper-evidence "
        "chain requires byte-exact canonical bytes for this artifact.",
    ),
}


def _consumer_registry_problems(
    registry: Mapping[str, CanonicalArtifactDef],
    sites: Counter[tuple[str, str, bool]],
) -> list[str]:
    """Compare strict call counts to registered path-and-scope consumer counts."""
    strict = Counter(
        {
            (path, qualname): count
            for (path, qualname, is_strict), count in sites.items()
            if is_strict
        }
    )
    expected = Counter(
        (entry.consumer_path, entry.consumer_function) for entry in registry.values()
    )
    problems: list[str] = []

    for (path, qualname), expected_count, observed_count in count_differences(expected, strict):
        if expected_count > observed_count:
            kinds = sorted(
                kind
                for kind, entry in registry.items()
                if (entry.consumer_path, entry.consumer_function) == (path, qualname)
            )
            problems.append(
                f"{', '.join(kinds)}: {path}::{qualname} is missing count: "
                f"{expected_count - observed_count}; "
                "if the consumer moved or was renamed, update consumer_path/consumer_function; "
                "if it no longer exists, delete the entry"
            )
        else:
            problems.append(
                f"{path}::{qualname} has unregistered count: {observed_count - expected_count}; "
                "register a CanonicalArtifactDef in _CANONICAL_JSON_ARTIFACT_REGISTRY "
                "naming its producer"
            )
    return sorted(problems)


def _non_canonical_exception_problems(
    exceptions: Mapping[str, tuple[tuple[str, str], str]],
    sites: Counter[tuple[str, str, bool]],
) -> list[str]:
    """Check scope-wide exceptions for a reason, decode call, and no strict calls.

    An exception requires at least one decode call and rejects any strict call in its
    scope. It does not associate individual non-strict calls with separate artifact
    names or enforce their multiplicity; the reason must cover the scope's non-canonical
    reads.
    """
    problems: list[str] = []
    for artifact_name, ((path, qualname), reason) in exceptions.items():
        if not reason:
            problems.append(f"{artifact_name} has an empty exception reason")
        scoped_calls = [
            (is_strict, count)
            for (site_path, site_qualname, is_strict), count in sites.items()
            if (site_path, site_qualname) == (path, qualname) and count
        ]
        if not scoped_calls:
            problems.append(
                f"{artifact_name}: no decode_versioned_json_bytes call in "
                f"{path}::{qualname} — update the anchor if it moved, delete the entry if removed"
            )
        elif any(is_strict for is_strict, _count in scoped_calls):
            problems.append(
                f"{artifact_name}: {path}::{qualname} now counts as strict "
                "(require_canonical=True, a nonliteral argument, or **kwargs) — this pairing "
                "is documented as non-canonical in _NON_CANONICAL_JSON_EXCEPTIONS; "
                "register its producer in _CANONICAL_JSON_ARTIFACT_REGISTRY if strictness is "
                "intended, otherwise restore an explicitly non-canonical call"
            )
    return sorted(problems)


class TestCanonicalJsonProducerConvention:
    def test_authority_has_one_materializer_owned_producer(self):
        authority = _CANONICAL_JSON_ARTIFACT_REGISTRY["authority"]

        assert authority.producer_symbol == "_write_or_verify"
        assert authority.producer_path == _MATERIALIZER_PRODUCER_PATH
        assert _scan_child_tool_authority_producer_sites() == set()

    def test_require_canonical_consumers_have_registered_producers(self):
        """Every strict consumer scope matches one registry entry in both directions."""
        problems = _consumer_registry_problems(
            _CANONICAL_JSON_ARTIFACT_REGISTRY, _scan_decode_call_sites()
        )
        assert not problems, "\n\n".join(problems)

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

    def test_new_require_canonical_consumer_without_registered_producer_fails(self):
        """Meta-test: a fake extra consumer site should cause the ratchet to fail."""
        sites = _scan_decode_call_sites()
        sites[("src/autoskillit/fake_canonical_module.py", "load", True)] += 1
        problems = _consumer_registry_problems(_CANONICAL_JSON_ARTIFACT_REGISTRY, sites)
        assert any("fake_canonical_module" in problem for problem in problems)

    def test_non_canonical_exceptions_still_resolve_to_non_canonical_reads(self):
        """Each exception scope has non-strict decode calls and a reason."""
        repo_root = Path(__file__).resolve().parents[2]
        sites = Counter()
        exception_paths = sorted(
            {path for (path, _qualname), _reason in _NON_CANONICAL_JSON_EXCEPTIONS.values()}
        )
        for relative_path in exception_paths:
            source_path = repo_root / relative_path
            tree = ast.parse(source_path.read_text(), filename=str(source_path))
            sites.update(_decode_call_sites(tree, relative_path))
        problems = _non_canonical_exception_problems(_NON_CANONICAL_JSON_EXCEPTIONS, sites)
        assert not problems, "\n\n".join(problems)


def _def(path: str, function: str) -> CanonicalArtifactDef:
    """Build a synthetic consumer entry; producer fields are ignored by this diff."""
    return CanonicalArtifactDef(
        consumer_path=path,
        consumer_function=function,
        producer_symbol="write_placeholder",
        producer_path="pkg/producer.py",
        producer_function="write_placeholder",
        skill_md_refs=(),
    )


class TestCanonicalConsumerGuardBehavior:
    def test_consumer_identity_is_line_free_and_scope_qualified(self):
        source = """
def load():
    decode_versioned_json_bytes(payload, require_canonical=True)

class Verifier:
    def load(self):
        decode_versioned_json_bytes(payload, require_canonical=True)

    async def aload(self):
        decode_versioned_json_bytes(payload, require_canonical=True)

    decode_versioned_json_bytes(payload, require_canonical=True)

def outer():
    def inner():
        decode_versioned_json_bytes(payload, require_canonical=True)

decode_versioned_json_bytes(payload, require_canonical=True)

def HeaderOwner(value=header_probe()):
    pass

class ClassHeaderOwner(header_probe()):
    pass
"""
        tree = ast.parse(source)
        expected = Counter(
            {
                ("pkg/mod.py", "load", True): 1,
                ("pkg/mod.py", "Verifier.load", True): 1,
                ("pkg/mod.py", "Verifier.aload", True): 1,
                ("pkg/mod.py", "Verifier", True): 1,
                ("pkg/mod.py", "outer.inner", True): 1,
                ("pkg/mod.py", MODULE_SCOPE, True): 1,
            }
        )
        assert _decode_call_sites(tree, "pkg/mod.py") == expected

        owners = scope_qualnames(tree)
        header_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "header_probe"
        ]
        assert {owners[node] for node in header_calls} == {"HeaderOwner", "ClassHeaderOwner"}

        shifted_tree = ast.parse("\n" * 40 + "# shifted line numbers\n" + source)
        assert _decode_call_sites(shifted_tree, "pkg/mod.py") == expected

    @pytest.mark.parametrize(
        "load_b_def",
        [
            "",
            "    def load_b(self):\n"
            "        decode_versioned_json_bytes(payload, require_canonical=False)",
            "    def load_b(self):\n        decode_versioned_json_bytes(payload)",
        ],
        ids=["removed", "non-canonical", "keyword-omitted"],
    )
    def test_registered_consumer_missing_or_noncanonical_is_rejected_beside_valid_sibling(
        self, load_b_def: str
    ):
        source = f"""
class Verifier:
    def load_a(self):
        decode_versioned_json_bytes(payload, require_canonical=True)
{load_b_def}
"""
        registry = {
            "alpha": _def("pkg/mod.py", "Verifier.load_a"),
            "beta": _def("pkg/mod.py", "Verifier.load_b"),
        }
        problems = _consumer_registry_problems(
            registry, _decode_call_sites(ast.parse(source), "pkg/mod.py")
        )

        assert len(problems) == 1
        problem = problems[0]
        assert "beta" in problem
        assert "pkg/mod.py::Verifier.load_b" in problem
        assert "alpha" not in problem
        assert "update consumer_path/consumer_function" in problem
        assert "delete the entry" in problem

    def test_extra_strict_calls_are_rejected(self):
        registry = {"alpha": _def("pkg/mod.py", "Verifier.load_a")}
        extra_scope = ast.parse(
            """
class Verifier:
    def load_a(self):
        decode_versioned_json_bytes(payload, require_canonical=True)

def load_c():
    decode_versioned_json_bytes(payload, require_canonical=True)
"""
        )
        problems = _consumer_registry_problems(
            registry, _decode_call_sites(extra_scope, "pkg/mod.py")
        )
        assert len(problems) == 1
        assert "pkg/mod.py::load_c" in problems[0]
        assert "register a CanonicalArtifactDef" in problems[0]

        duplicate_scope = ast.parse(
            """
class Verifier:
    def load_a(self):
        decode_versioned_json_bytes(first, require_canonical=True)
        decode_versioned_json_bytes(second, require_canonical=True)
"""
        )
        problems = _consumer_registry_problems(
            registry, _decode_call_sites(duplicate_scope, "pkg/mod.py")
        )
        assert len(problems) == 1
        assert "pkg/mod.py::Verifier.load_a" in problems[0]
        assert "unregistered count: 1" in problems[0]

    def test_ambiguous_require_canonical_counts_as_strict(self):
        tree = ast.parse(
            """
def load_x(flag):
    decode_versioned_json_bytes(payload, require_canonical=flag)

def load_y(opts):
    decode_versioned_json_bytes(payload, **opts)

def load_z(opts):
    decode_versioned_json_bytes(payload, require_canonical=False, **opts)
"""
        )
        sites = _decode_call_sites(tree, "pkg/mod.py")
        assert sites == Counter(
            {
                ("pkg/mod.py", "load_x", True): 1,
                ("pkg/mod.py", "load_y", True): 1,
                ("pkg/mod.py", "load_z", True): 1,
            }
        )
        problems = _consumer_registry_problems({}, sites)
        assert len(problems) == 3
        assert all("unregistered count: 1" in problem for problem in problems)
        assert all(
            f"pkg/mod.py::{function}" in "\n".join(problems)
            for function in ("load_x", "load_y", "load_z")
        )

    def test_non_canonical_exception_detects_flip_and_disappearance(self):
        exceptions = {"report.json": (("pkg/mod.py", "verify"), "reason text")}
        non_strict = _decode_call_sites(
            ast.parse(
                """
def verify():
    decode_versioned_json_bytes(payload, require_canonical=False)
"""
            ),
            "pkg/mod.py",
        )
        assert _non_canonical_exception_problems(exceptions, non_strict) == []

        strict = _decode_call_sites(
            ast.parse(
                """
def verify():
    decode_versioned_json_bytes(payload, require_canonical=True)
"""
            ),
            "pkg/mod.py",
        )
        problems = _non_canonical_exception_problems(exceptions, strict)
        assert len(problems) == 1
        assert "report.json" in problems[0]
        assert "counts as strict" in problems[0]

        missing = _decode_call_sites(ast.parse("def verify():\n    pass\n"), "pkg/mod.py")
        problems = _non_canonical_exception_problems(exceptions, missing)
        assert len(problems) == 1
        assert "report.json" in problems[0]
        assert "no decode_versioned_json_bytes call" in problems[0]

        empty_reason = {"report.json": (("pkg/mod.py", "verify"), "")}
        problems = _non_canonical_exception_problems(empty_reason, non_strict)
        assert len(problems) == 1
        assert "report.json" in problems[0]
        assert "empty exception reason" in problems[0]

    def test_source_parse_failure_is_not_silently_skipped(self, tmp_path, monkeypatch):
        source_root = tmp_path / "src" / "autoskillit"
        source_root.mkdir(parents=True)
        invalid_file = source_root / "broken.py"
        invalid_file.write_text("def broken(:\n", encoding="utf-8")
        monkeypatch.setattr(
            "tests.infra.test_canonical_json_producer_convention._REPO_ROOT", tmp_path
        )
        monkeypatch.setattr(
            "tests.infra.test_canonical_json_producer_convention._SRC_ROOT", source_root
        )

        with pytest.raises(SyntaxError, match="broken.py"):
            _scan_decode_call_sites()
