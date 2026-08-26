"""Stable facade re-exporting collector extractor implementations.

Decomposed from the original 853-line ``collectors/extractors.py`` per #4836.
Importing shard symbols directly is fine; importing them through this facade
guarantees the public surface (``autoskillit.exploration.collectors.extractors.X``)
survives future shard reorganisation.

This facade also re-exports the internal helpers ``test_bounded_collectors.py``
accesses via the module alias (``extractors_module._collector_metadata``,
``extractors_module._bounded_diagnostic_text``). Removing these would break the
existing test suite without modifying any source files.
"""

from .._bounded import run_bounded_rg  # noqa: F401  tests pull via module alias
from ._evidence import (
    _bounded_diagnostic_text,  # noqa: F401  tests pull via module alias
    _collector_metadata,  # noqa: F401  tests pull via module alias
    _invalid_rg_json_diagnostic,  # noqa: F401  re-exported for sibling shards
    _report,  # noqa: F401  re-exported for sibling shards
    collector_manifest_digest,
)
from ._file_search import (
    _normalise_scope,  # noqa: F401  re-exported for sibling shards
    _scoped_paths,  # noqa: F401  re-exported for sibling shards
    collect_artifact,
    collect_file_list,
    collect_search,
)
from ._observational import (
    _relabel,  # noqa: F401  re-exported for sibling shards
    collect_architecture,
    collect_autoskillit_registry,
    collect_autoskillit_toml,
    collect_coverage_observation,
    collect_generated_artifact,
    collect_observational_artifact,
    collect_python_stub,
    collect_test_map_observation,
    collect_unsupported,
)
from ._python_ast import (
    _is_named_base,  # noqa: F401  re-exported for sibling shards
    _qualified_name,  # noqa: F401  re-exported for sibling shards
    collect_python_ast,
)
from ._records import CollectorInvocation, CollectorProfile
from ._registry import COLLECTOR_PROFILES

__all__ = [
    "COLLECTOR_PROFILES",
    "CollectorInvocation",
    "CollectorProfile",
    "collect_architecture",
    "collect_artifact",
    "collect_autoskillit_registry",
    "collect_autoskillit_toml",
    "collect_coverage_observation",
    "collect_file_list",
    "collect_generated_artifact",
    "collect_python_ast",
    "collect_python_stub",
    "collect_search",
    "collect_test_map_observation",
    "collect_unsupported",
    "collector_manifest_digest",
]
