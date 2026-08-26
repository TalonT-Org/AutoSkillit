"""``COLLECTOR_PROFILES`` registry data and invocation factories.

Decomposed from the original ``collectors/extractors.py`` per #4836. The
13-entry tuple below is byte-for-byte identical to the original; the collector
manifest digest is a stable signature of this exact ordering.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from autoskillit.core import RepositoryProfileId

from .._bounded import CollectorLimits
from ._file_search import collect_artifact, collect_file_list, collect_search
from ._observational import (
    collect_architecture,
    collect_autoskillit_registry,
    collect_autoskillit_toml,
    collect_coverage_observation,
    collect_generated_artifact,
    collect_python_stub,
    collect_test_map_observation,
    collect_unsupported,
)
from ._python_ast import collect_python_ast
from ._records import (
    CollectorInvocation,
    CollectorProfile,
    _PerScopeCollector,
)

if TYPE_CHECKING:
    from ._records import _InvocationReports

__all__ = [
    "COLLECTOR_PROFILES",
    "_per_scope_invocation",
    "_search_invocation",
    "_unsupported_invocation",
]


def _per_scope_invocation(
    collector_id: str,
    collect: _PerScopeCollector,
) -> CollectorInvocation:
    def invoke(
        root: Path,
        snapshot_digest: str,
        query: str,
        scopes: tuple[str, ...],
        limits: CollectorLimits,
    ) -> _InvocationReports:
        del query
        return tuple(
            (
                (scope or ".",),
                collect(root, snapshot_digest, scope, limits),
            )
            for scope in (scopes or ("",))
        )

    return CollectorInvocation(collector_id, "per-scope.v1", invoke)


def _search_invocation() -> CollectorInvocation:
    def invoke(
        root: Path,
        snapshot_digest: str,
        query: str,
        scopes: tuple[str, ...],
        limits: CollectorLimits,
    ) -> _InvocationReports:
        return (
            (
                scopes or (".",),
                collect_search(
                    root,
                    snapshot_digest,
                    query.strip(),
                    limits,
                    scopes=scopes,
                ),
            ),
        )

    return CollectorInvocation("bounded-rg-search", "one-call-multi-scope-query.v1", invoke)


def _unsupported_invocation(collector_id: str) -> CollectorInvocation:
    def invoke(
        root: Path,
        snapshot_digest: str,
        query: str,
        scopes: tuple[str, ...],
        limits: CollectorLimits,
    ) -> _InvocationReports:
        del query
        return tuple(
            (
                (scope or ".",),
                collect_unsupported(
                    root,
                    snapshot_digest,
                    limits,
                    collector_id=collector_id,
                ),
            )
            for scope in (scopes or ("",))
        )

    return CollectorInvocation(collector_id, "fixed-unsupported-per-scope.v1", invoke)


COLLECTOR_PROFILES: Final = (
    CollectorProfile(
        "contained-artifact",
        "bounded-file-read",
        _per_scope_invocation("contained-artifact", collect_artifact),
        RepositoryProfileId.LANGUAGE_NEUTRAL,
    ),
    CollectorProfile(
        "contained-list",
        "contained-walk",
        _per_scope_invocation("contained-list", collect_file_list),
        RepositoryProfileId.LANGUAGE_NEUTRAL,
        required_by_default=True,
    ),
    CollectorProfile(
        "bounded-rg-search",
        "rg-no-config-no-follow",
        _search_invocation(),
        RepositoryProfileId.LANGUAGE_NEUTRAL,
        required_by_default=True,
    ),
    CollectorProfile(
        "python-ast",
        "stdlib-ast",
        _per_scope_invocation("python-ast", collect_python_ast),
        RepositoryProfileId.GENERIC_PYTHON,
        required_by_default=True,
    ),
    CollectorProfile(
        "native-lsp",
        "unsupported",
        _unsupported_invocation("native-lsp"),
        RepositoryProfileId.GENERIC_PYTHON,
    ),
    CollectorProfile(
        "native-tree-sitter",
        "unsupported",
        _unsupported_invocation("native-tree-sitter"),
        RepositoryProfileId.GENERIC_PYTHON,
    ),
    CollectorProfile(
        "autoskillit-registry",
        "stdlib-ast",
        _per_scope_invocation("autoskillit-registry", collect_autoskillit_registry),
        RepositoryProfileId.AUTOSKILLIT,
        required_by_default=True,
    ),
    CollectorProfile(
        "autoskillit-manifest",
        "tomllib",
        _per_scope_invocation("autoskillit-manifest", collect_autoskillit_toml),
        RepositoryProfileId.AUTOSKILLIT,
    ),
    CollectorProfile(
        "autoskillit-architecture",
        "bounded-file-read",
        _per_scope_invocation("autoskillit-architecture", collect_architecture),
        RepositoryProfileId.AUTOSKILLIT,
    ),
    CollectorProfile(
        "python-stub",
        "bounded-file-read",
        _per_scope_invocation("python-stub", collect_python_stub),
        RepositoryProfileId.GENERIC_PYTHON,
    ),
    CollectorProfile(
        "generated-artifact",
        "bounded-file-read",
        _per_scope_invocation("generated-artifact", collect_generated_artifact),
        RepositoryProfileId.LANGUAGE_NEUTRAL,
    ),
    CollectorProfile(
        "coverage-observation",
        "bounded-file-read",
        _per_scope_invocation("coverage-observation", collect_coverage_observation),
        RepositoryProfileId.LANGUAGE_NEUTRAL,
    ),
    CollectorProfile(
        "test-map-observation",
        "bounded-file-read",
        _per_scope_invocation("test-map-observation", collect_test_map_observation),
        RepositoryProfileId.LANGUAGE_NEUTRAL,
    ),
)
