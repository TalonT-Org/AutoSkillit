"""Stable facade re-exporting collector extractor implementations.

Decomposed from the original 853-line ``collectors/extractors.py`` per #4836.
Importing shard symbols directly is fine; importing them through this facade
guarantees the public surface (``autoskillit.exploration.collectors.extractors.X``)
survives future shard reorganisation.

The ``# noqa: F401`` re-exports below are test-monkeypatch anchors: the test
suite reaches into this module via ``extractors_module.NAME`` to swap helpers
for test fixtures. Sibling shards import directly from each other rather than
going through this facade.
"""

from .._bounded import run_bounded_rg  # noqa: F401  tests pull via module alias
from ._evidence import (
    _bounded_diagnostic_text,  # noqa: F401  tests pull via module alias
    _collector_metadata,  # noqa: F401  tests pull via module alias
    collector_manifest_digest,
)
from ._file_search import (
    collect_artifact,
    collect_file_list,
    collect_search,
)
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
