"""Deterministic, non-executing repository collector profiles."""

from __future__ import annotations

import ast
import hashlib
import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Final

from autoskillit.core import (
    CollectorReport,
    CollectorStatus,
    EvidenceRecord,
    MethodProvenance,
    NodeKey,
    RepositoryProfileId,
)

from ..graph import SubjectNamespace
from ._bounded import (
    CollectorLimits,
    CollectorSafetyError,
    list_contained_files,
    read_contained_file,
    run_bounded_rg,
)

_COLLECTOR_VERSION: Final = "autoskillit.collector-extractors.v2"
_OBSERVATION_UNCERTAINTY: Final = (
    "collector observations do not establish semantic relationships",
)


@dataclass(frozen=True, slots=True)
class CollectorProfile:
    collector_id: str
    method: str
    collect: Callable[[Path, str, str, CollectorLimits], CollectorReport]
    profile: RepositoryProfileId
    version: str = _COLLECTOR_VERSION
    required_by_default: bool = False


def collector_manifest_digest(
    profiles: tuple[CollectorProfile, ...] | None = None,
) -> str:
    """Return the versioned identity of exactly the collectors this process registers."""

    registry = COLLECTOR_PROFILES if profiles is None else profiles
    records = [
        {
            "id": profile.collector_id,
            "method": profile.method,
            "profile": profile.profile.value,
            "required_by_default": profile.required_by_default,
            "version": profile.version,
        }
        for profile in sorted(registry, key=lambda item: item.collector_id)
    ]
    if len({record["id"] for record in records}) != len(records):
        raise ValueError("collector manifest contains duplicate collector identifiers")
    encoded = json.dumps(records, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(
        b"autoskillit.collector-manifest.v1\0" + encoded.encode("ascii")
    ).hexdigest()


def _report(
    collector_id: str,
    snapshot_digest: str,
    status: CollectorStatus,
    diagnostics: tuple[str, ...] = (),
    evidence: tuple[EvidenceRecord, ...] = (),
) -> CollectorReport:
    return CollectorReport(
        collector_id, status, snapshot_digest, evidence, "; ".join(diagnostics) or None
    )


def _evidence(
    collector_id: str, snapshot_digest: str, path: str, line: int, excerpt: str
) -> EvidenceRecord:
    claim = excerpt
    location = f"{path}:{line}"
    method, version = _collector_metadata(collector_id)
    digest = hashlib.sha256(claim.encode("utf-8", "surrogateescape")).hexdigest()
    identifier = hashlib.sha256(
        f"{collector_id}\0{method}\0{version}\0{path}\0{line}\0{claim}\0{digest}".encode(
            "utf-8", "surrogateescape"
        )
    ).hexdigest()
    return EvidenceRecord(
        identifier,
        MethodProvenance.COLLECTOR,
        snapshot_digest,
        subject=NodeKey("repository-path", path),
        facts=(claim,),
        locator=location,
        method=method,
        extractor_version=version,
        searched_scope=(path,),
        location=location,
        query_uncertainty=_OBSERVATION_UNCERTAINTY,
    )


def _normalise_scope(scope: str) -> str:
    """Accept only a repository-relative path prefix for scoped observations."""

    if not scope or scope == ".":
        return ""
    candidate = PurePosixPath(scope)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or ".." in candidate.parts
        or any(part in {"", "."} for part in candidate.parts)
        or any(any(character in part for character in "*?[]!{}\\") for part in candidate.parts)
    ):
        raise CollectorSafetyError("collector scope must be a contained literal path")
    return candidate.as_posix()


def _scoped_paths(root: Path, scope: str, limits: CollectorLimits) -> tuple[str, ...]:
    prefix = _normalise_scope(scope)
    paths = list_contained_files(root, limits)
    if not prefix:
        return paths
    return tuple(path for path in paths if path == prefix or path.startswith(f"{prefix}/"))


def _qualified_name(node: ast.expr) -> str | None:
    """Return the static spelling of a simple name or attribute access."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return None if parent is None else f"{parent}.{node.attr}"
    return None


def _is_named_base(node: ast.expr, names: frozenset[str]) -> bool:
    qualified = _qualified_name(node)
    return qualified is not None and qualified.rsplit(".", maxsplit=1)[-1] in names


def collect_artifact(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    collector_id = "contained-artifact"
    try:
        payload = read_contained_file(root, path, limits)
    except CollectorSafetyError as exc:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (str(exc),))
    return _report(
        collector_id,
        snapshot_digest,
        CollectorStatus.SUCCEEDED,
        evidence=(
            _evidence(
                collector_id,
                snapshot_digest,
                path,
                1,
                payload.decode("utf-8", "replace")[: limits.max_output_bytes],
            ),
        ),
    )


def collect_file_list(
    root: Path, snapshot_digest: str, scope: str, limits: CollectorLimits
) -> CollectorReport:
    collector_id = "contained-list"
    try:
        paths = _scoped_paths(root, scope, limits)
    except CollectorSafetyError as exc:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (str(exc),))
    evidence = tuple(_evidence(collector_id, snapshot_digest, path, 1, path) for path in paths)
    return _report(collector_id, snapshot_digest, CollectorStatus.SUCCEEDED, evidence=evidence)


def collect_search(
    root: Path,
    snapshot_digest: str,
    pattern: str,
    limits: CollectorLimits,
    *,
    scopes: tuple[str, ...] = (),
) -> CollectorReport:
    collector_id = "bounded-rg-search"
    try:
        normalized_scopes = tuple(_normalise_scope(scope) for scope in scopes)
    except CollectorSafetyError as exc:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (str(exc),))
    globs = tuple(
        scope if (root / scope).is_file() else f"{scope}/**"
        for scope in normalized_scopes
        if scope
    )
    result = run_bounded_rg(root, pattern, globs=globs, limits=limits)
    if result.failure is not None:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (result.failure,))
    evidence: list[EvidenceRecord] = []
    for raw_line in result.stdout.splitlines():
        if len(evidence) >= limits.max_matches:
            return _report(
                collector_id,
                snapshot_digest,
                CollectorStatus.TRUNCATED,
                ("match limit exceeded",),
                tuple(evidence),
            )
        try:
            event = json.loads(raw_line)
            data = event["data"]
            if event["type"] != "match":
                continue
            path = data["path"]["text"]
            line = data["line_number"]
            text = data["lines"]["text"].rstrip("\n")
        except (KeyError, TypeError, ValueError):
            return _report(
                collector_id,
                snapshot_digest,
                CollectorStatus.FAILED,
                ("invalid rg json output",),
                tuple(evidence),
            )
        evidence.append(
            replace(
                _evidence(collector_id, snapshot_digest, path, line, text),
                searched_scope=normalized_scopes or (".",),
            )
        )
    status = CollectorStatus.SUCCEEDED if result.returncode in (0, 1) else CollectorStatus.FAILED
    return _report(
        collector_id,
        snapshot_digest,
        status,
        () if status is CollectorStatus.SUCCEEDED else ("rg failed",),
        tuple(evidence),
    )


def collect_python_ast(
    root: Path, snapshot_digest: str, scope: str, limits: CollectorLimits
) -> CollectorReport:
    collector_id = "python-ast"
    evidence: list[EvidenceRecord] = []

    def truncated_report() -> CollectorReport:
        return _report(
            collector_id,
            snapshot_digest,
            CollectorStatus.TRUNCATED,
            ("symbol limit exceeded",),
            tuple(evidence),
        )

    def observe(
        subject: NodeKey,
        path: str,
        line: int,
        claim: str,
        *,
        unknowns: tuple[str, ...] = (),
    ) -> bool:
        evidence.append(
            replace(
                _evidence(collector_id, snapshot_digest, path, line, claim),
                subject=subject,
                unknowns=unknowns,
            )
        )
        return len(evidence) >= limits.max_matches

    try:
        paths = tuple(path for path in _scoped_paths(root, scope, limits) if path.endswith(".py"))
        for path in paths:
            source = read_contained_file(root, path, limits).decode("utf-8", "replace")
            tree = ast.parse(source, filename=path, type_comments=True)
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    namespace = SubjectNamespace.PYTHON_SYMBOL
                    if isinstance(node, ast.ClassDef):
                        if any(
                            _is_named_base(base, frozenset({"Protocol"})) for base in node.bases
                        ):
                            namespace = SubjectNamespace.PYTHON_PROTOCOL
                        elif any(
                            _is_named_base(base, frozenset({"ABC", "ABCMeta"}))
                            for base in node.bases
                        ):
                            namespace = SubjectNamespace.PYTHON_NOMINAL_PROTOCOL
                    if observe(
                        NodeKey(namespace.value, f"{path}:{node.lineno}:{node.name}"),
                        path,
                        node.lineno,
                        node.name,
                    ):
                        return truncated_report()
                    for decorator in node.decorator_list:
                        decorated = (
                            decorator.func if isinstance(decorator, ast.Call) else decorator
                        )
                        decorator_name = _qualified_name(decorated)
                        if (
                            decorator_name is not None
                            and decorator_name.rsplit(".", maxsplit=1)[-1] in {"override", "patch"}
                            and observe(
                                NodeKey(
                                    SubjectNamespace.PYTHON_RUNTIME_PATCH.value,
                                    f"{path}:{node.lineno}:{decorator_name}",
                                ),
                                path,
                                node.lineno,
                                f"decorator {decorator_name}",
                            )
                        ):
                            return truncated_report()
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if observe(
                            NodeKey(SubjectNamespace.PYTHON_IMPORT.value, alias.name),
                            path,
                            node.lineno,
                            f"import {alias.name}",
                        ):
                            return truncated_report()
                elif isinstance(node, ast.ImportFrom):
                    module = f"{'.' * node.level}{node.module or ''}"
                    namespace = (
                        SubjectNamespace.PYTHON_REEXPORT
                        if path.endswith("__init__.py")
                        else SubjectNamespace.PYTHON_IMPORT
                    )
                    for alias in node.names:
                        if observe(
                            NodeKey(namespace.value, f"{module}:{alias.name}"),
                            path,
                            node.lineno,
                            f"from {module} import {alias.name}",
                        ):
                            return truncated_report()
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and isinstance(
                            node.value, (ast.Name, ast.Attribute)
                        ):
                            if observe(
                                NodeKey(
                                    SubjectNamespace.PYTHON_ALIAS.value,
                                    f"{path}:{target.id}",
                                ),
                                path,
                                node.lineno,
                                f"alias {target.id}",
                            ):
                                return truncated_report()
                        if isinstance(target, ast.Name) and "registry" in target.id.lower():
                            if observe(
                                NodeKey(
                                    SubjectNamespace.PYTHON_REGISTRY.value,
                                    f"{path}:{node.lineno}:{target.id}",
                                ),
                                path,
                                node.lineno,
                                f"registry {target.id}",
                            ):
                                return truncated_report()
                        elif isinstance(target, ast.Attribute):
                            target_name = _qualified_name(target)
                            if target_name is not None and observe(
                                NodeKey(
                                    SubjectNamespace.PYTHON_RUNTIME_WIRING.value,
                                    f"{path}:{node.lineno}:{target_name}",
                                ),
                                path,
                                node.lineno,
                                f"wiring {target_name}",
                            ):
                                return truncated_report()
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_DECLARATION.value,
                            f"{path}:{node.lineno}:{node.target.id}",
                        ),
                        path,
                        node.lineno,
                        f"declaration {node.target.id}",
                    ):
                        return truncated_report()
                elif isinstance(node, ast.Call):
                    call_name = _qualified_name(node.func)
                    if call_name is None:
                        continue
                    if observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_CALL.value,
                            f"{path}:{node.lineno}:{call_name}",
                        ),
                        path,
                        node.lineno,
                        f"call {call_name}",
                    ):
                        return truncated_report()
                    terminal_name = call_name.rsplit(".", maxsplit=1)[-1]
                    if terminal_name in {"import_module", "__import__"}:
                        import_name = (
                            node.args[0].value
                            if node.args
                            and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)
                            else "<unresolved>"
                        )
                        if observe(
                            NodeKey(SubjectNamespace.PYTHON_DYNAMIC_IMPORT.value, import_name),
                            path,
                            node.lineno,
                            f"dynamic import {import_name}",
                            unknowns=("dynamic import target is not statically resolved",)
                            if import_name == "<unresolved>"
                            else (),
                        ):
                            return truncated_report()
                    if terminal_name in {"register", "setattr", "wire"} and observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_RUNTIME_WIRING.value,
                            f"{path}:{node.lineno}:{call_name}",
                        ),
                        path,
                        node.lineno,
                        f"runtime wiring {call_name}",
                    ):
                        return truncated_report()
                    if terminal_name in {"override", "patch", "setattr"} and observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_RUNTIME_PATCH.value,
                            f"{path}:{node.lineno}:{call_name}",
                        ),
                        path,
                        node.lineno,
                        f"runtime patch {call_name}",
                    ):
                        return truncated_report()
                    if (
                        path.startswith("tests/") or Path(path).name.startswith("test_")
                    ) and observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_TEST_CONSUMER.value,
                            f"{path}:{node.lineno}:{call_name}",
                        ),
                        path,
                        node.lineno,
                        f"test consumer {call_name}",
                    ):
                        return truncated_report()
    except (CollectorSafetyError, SyntaxError) as exc:
        return _report(
            collector_id,
            snapshot_digest,
            CollectorStatus.FAILED,
            (str(exc),),
            tuple(evidence),
        )
    return _report(
        collector_id, snapshot_digest, CollectorStatus.SUCCEEDED, evidence=tuple(evidence)
    )


def collect_unsupported(
    root: Path, snapshot_digest: str, scope: str, limits: CollectorLimits
) -> CollectorReport:
    del root, limits
    collector_id = scope
    return _report(
        collector_id,
        snapshot_digest,
        CollectorStatus.UNSUPPORTED,
        ("native capability is not available in the collector runtime",),
    )


def collect_autoskillit_toml(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    collector_id = "autoskillit-manifest"
    try:
        data = tomllib.loads(read_contained_file(root, path, limits).decode("utf-8"))
    except (CollectorSafetyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (str(exc),))
    excerpt = json.dumps(data, sort_keys=True, default=str)[: limits.max_output_bytes]
    return _report(
        collector_id,
        snapshot_digest,
        CollectorStatus.SUCCEEDED,
        evidence=(
            replace(
                _evidence(
                    collector_id,
                    snapshot_digest,
                    path,
                    1,
                    excerpt,
                ),
                subject=NodeKey(SubjectNamespace.CONFIGURATION_DECLARATION.value, path),
            ),
        ),
    )


def collect_observational_artifact(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    """Read coverage and test-map artifacts without interpreting them as execution truth."""

    return collect_artifact(root, snapshot_digest, path, limits)


def _relabel(
    report: CollectorReport,
    collector_id: str,
    *,
    subject_namespace: SubjectNamespace | None = None,
) -> CollectorReport:
    method, version = _collector_metadata(collector_id)
    evidence = tuple(
        replace(
            record,
            evidence_id=hashlib.sha256(
                f"{collector_id}\0{record.evidence_id}".encode()
            ).hexdigest(),
            provenance=MethodProvenance.COLLECTOR,
            method=method,
            extractor_version=version,
            subject=(
                NodeKey(subject_namespace.value, record.subject.value)
                if subject_namespace is not None and record.subject is not None
                else record.subject
            ),
        )
        for record in report.evidence
    )
    return replace(report, collector_id=collector_id, evidence=evidence)


def collect_autoskillit_registry(
    root: Path, snapshot_digest: str, scope: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_python_ast(root, snapshot_digest, scope, limits), "autoskillit-registry"
    )


def collect_architecture(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_artifact(root, snapshot_digest, path, limits), "autoskillit-architecture"
    )


def collect_python_stub(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    if not path.endswith(".pyi"):
        return _report(
            "python-stub",
            snapshot_digest,
            CollectorStatus.FAILED,
            ("python stub path must end in .pyi",),
        )
    return _relabel(collect_artifact(root, snapshot_digest, path, limits), "python-stub")


def collect_generated_artifact(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_artifact(root, snapshot_digest, path, limits),
        "generated-artifact",
        subject_namespace=SubjectNamespace.GENERATED_ARTIFACT,
    )


def collect_coverage_observation(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_observational_artifact(root, snapshot_digest, path, limits),
        "coverage-observation",
        subject_namespace=SubjectNamespace.COVERAGE_OBSERVATION,
    )


def collect_test_map_observation(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_observational_artifact(root, snapshot_digest, path, limits),
        "test-map-observation",
        subject_namespace=SubjectNamespace.TEST_CONSUMER,
    )


COLLECTOR_PROFILES: Final = (
    CollectorProfile(
        "contained-artifact",
        "bounded-file-read",
        collect_artifact,
        RepositoryProfileId.LANGUAGE_NEUTRAL,
    ),
    CollectorProfile(
        "contained-list",
        "contained-walk",
        collect_file_list,
        RepositoryProfileId.LANGUAGE_NEUTRAL,
        required_by_default=True,
    ),
    CollectorProfile(
        "bounded-rg-search",
        "rg-no-config-no-follow",
        collect_search,
        RepositoryProfileId.LANGUAGE_NEUTRAL,
        required_by_default=True,
    ),
    CollectorProfile(
        "python-ast",
        "stdlib-ast",
        collect_python_ast,
        RepositoryProfileId.GENERIC_PYTHON,
        required_by_default=True,
    ),
    CollectorProfile(
        "native-lsp", "unsupported", collect_unsupported, RepositoryProfileId.GENERIC_PYTHON
    ),
    CollectorProfile(
        "native-tree-sitter",
        "unsupported",
        collect_unsupported,
        RepositoryProfileId.GENERIC_PYTHON,
    ),
    CollectorProfile(
        "autoskillit-registry",
        "stdlib-ast",
        collect_autoskillit_registry,
        RepositoryProfileId.AUTOSKILLIT,
        required_by_default=True,
    ),
    CollectorProfile(
        "autoskillit-manifest",
        "tomllib",
        collect_autoskillit_toml,
        RepositoryProfileId.AUTOSKILLIT,
    ),
    CollectorProfile(
        "autoskillit-architecture",
        "bounded-file-read",
        collect_architecture,
        RepositoryProfileId.AUTOSKILLIT,
    ),
    CollectorProfile(
        "python-stub", "bounded-file-read", collect_python_stub, RepositoryProfileId.GENERIC_PYTHON
    ),
    CollectorProfile(
        "generated-artifact",
        "bounded-file-read",
        collect_generated_artifact,
        RepositoryProfileId.LANGUAGE_NEUTRAL,
    ),
    CollectorProfile(
        "coverage-observation",
        "bounded-file-read",
        collect_coverage_observation,
        RepositoryProfileId.LANGUAGE_NEUTRAL,
    ),
    CollectorProfile(
        "test-map-observation",
        "bounded-file-read",
        collect_test_map_observation,
        RepositoryProfileId.LANGUAGE_NEUTRAL,
    ),
)


def _collector_metadata(collector_id: str) -> tuple[str, str]:
    profile = next(
        (profile for profile in COLLECTOR_PROFILES if profile.collector_id == collector_id),
        None,
    )
    if profile is None:
        return "collector", _COLLECTOR_VERSION
    return profile.method, profile.version
