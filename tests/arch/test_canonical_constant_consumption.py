"""Architectural invariant: every env-var-set constant must have a production consumer."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ENV_CANONICAL_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*(?:_ENV_FORWARD_VARS|_REQUIRED_ENV)$")


def _find_env_set_constants(constants_file: Path) -> list[str]:
    """Find all module-level names matching env-var-set patterns in the constants file."""
    tree = ast.parse(constants_file.read_text())
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _ENV_CANONICAL_PATTERN.match(node.target.id):
                names.append(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and _ENV_CANONICAL_PATTERN.match(target.id):
                    names.append(target.id)
    return names


def _build_production_importers(src_root: Path) -> dict[str, set[Path]]:
    """Index imported names by the production files that import them."""
    importers: dict[str, set[Path]] = {}
    for py_file in src_root.rglob("*.py"):
        if py_file.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    importers.setdefault(alias.name, set()).add(py_file)
                    if alias.asname:
                        importers.setdefault(alias.asname, set()).add(py_file)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    importers.setdefault(alias.name, set()).add(py_file)
    return importers


def _has_production_import(
    importers: dict[str, set[Path]],
    constant_name: str,
    definition_file: Path,
    *,
    excluded_files: frozenset[Path] = frozenset(),
) -> bool:
    """Check if any production file (excluding the definition) imports the constant."""
    return bool(importers.get(constant_name, set()) - {definition_file} - excluded_files)


@pytest.fixture(scope="module")
def production_importers() -> dict[str, set[Path]]:
    """Build the production importer index once per worker fixture instance."""
    from autoskillit.core import paths

    return _build_production_importers(paths.pkg_root())


def _write_synthetic_module(src_root: Path, relative_path: str, source: str) -> Path:
    module_path = src_root / relative_path
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(source)
    return module_path


def test_production_importer_index_preserves_query_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src_root = tmp_path / "src"
    definition_file = _write_synthetic_module(
        src_root,
        "package/definition.py",
        "from package import DEFINITION_ONLY\n",
    )
    facade_file = _write_synthetic_module(
        src_root,
        "package/facade.py",
        "from package.definition import EXCLUDED_ONLY\n",
    )
    consumer_file = _write_synthetic_module(
        src_root,
        "package/consumer.py",
        """from typing import TYPE_CHECKING
from package.definition import EXTERNAL_CONSUMER
from package import ORIGINAL as LOCAL
import package.PLAIN_ORIGINAL as PLAIN_LOCAL

if TYPE_CHECKING:
    from package.definition import TYPE_CHECKING_ONLY
""",
    )
    _write_synthetic_module(
        src_root,
        "package/test_generated.py",
        "from package.definition import TEST_ONLY\n",
    )
    _write_synthetic_module(
        src_root,
        "package/invalid.py",
        "from package.definition import INVALID_ONLY\nif (\n",
    )

    real_parse = ast.parse
    parse_calls = 0

    def counting_parse(source: str) -> ast.Module:
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(source)

    monkeypatch.setattr(ast, "parse", counting_parse)
    importers = _build_production_importers(src_root)

    cases = [
        ("UNCONSUMED", frozenset(), False),
        ("DEFINITION_ONLY", frozenset(), False),
        ("EXCLUDED_ONLY", frozenset({facade_file}), False),
        ("EXTERNAL_CONSUMER", frozenset(), True),
        ("ORIGINAL", frozenset(), True),
        ("LOCAL", frozenset(), True),
        ("package.PLAIN_ORIGINAL", frozenset(), True),
        ("PLAIN_LOCAL", frozenset(), False),
        ("TYPE_CHECKING_ONLY", frozenset(), True),
        ("TEST_ONLY", frozenset(), False),
        ("INVALID_ONLY", frozenset(), False),
    ]
    for constant_name, excluded_files, expected in cases:
        assert (
            _has_production_import(
                importers,
                constant_name,
                definition_file,
                excluded_files=excluded_files,
            )
            is expected
        )

    assert importers["ORIGINAL"] == {consumer_file}
    assert importers["LOCAL"] == {consumer_file}
    assert importers["package.PLAIN_ORIGINAL"] == {consumer_file}
    assert "PLAIN_LOCAL" not in importers
    assert parse_calls == 4  # definition, facade, consumer, and invalid


def test_env_forward_constants_have_production_consumer(
    production_importers: dict[str, set[Path]],
) -> None:
    """Every env-var-set constant must be imported by at least one production module."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    constants_file = src_root / "core" / "types" / "_type_constants_env.py"
    constants = _find_env_set_constants(constants_file)
    assert constants, "No env-var-set constants found — test premise broken"

    unconsumed = [
        name
        for name in constants
        if not _has_production_import(production_importers, name, constants_file)
    ]
    assert not unconsumed, (
        f"Env-var-set constants (*_ENV_FORWARD_VARS / *_REQUIRED_ENV) with zero production "
        f"consumers: {unconsumed}. Each env-var-set constant must be imported and consumed "
        f"by production code to prevent dead-canonical-constant drift."
    )


_REGISTRY_CANONICAL_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*(?:_REGISTRY|_TOOLS|_TAGS|_NAMES)$")

_REGISTRY_EXEMPTIONS: dict[str, str] = {
    "FREE_RANGE_TOOLS": (
        "alias-derived: backing constant for UNGATED_TOOLS which is imported "
        "in pipeline/gate.py and server/tools/tools_recipe.py; "
        "direct import would be redundant"
    ),
    "FLEET_TOOLS": (
        "test-consumed: architectural enforcement constant imported by "
        "test_layer_enforcement.py, test_transforms_hygiene.py, and "
        "test_lifespan_fleet_boot.py for fleet tool-tag parity guards; "
        "no runtime production consumer needed"
    ),
    "FLEET_DISPATCH_TOOLS": (
        "alias-derived: subset of GATED_TOOLS exposed as a separate constant "
        "for session-type visibility dispatch; production consumers access "
        "it via GATED_TOOLS membership (see test_canonical_constant_consumption.py "
        "test_fleet_dispatch_tools_subset_of_gated_tools)"
    ),
    "KITCHEN_GATED_TOOLS": (
        "test-consumed: centralized visibility expected set imported by "
        "test_session_type_visibility.py; production kitchen-gated visibility "
        "is intentionally FastMCP tag-driven"
    ),
    "RECIPE_EXECUTION_INSTALL_SITE_REGISTRY": (
        "test-consumed: architectural ratchet imported by "
        "test_execution_install_delivery.py to bind every execution-install site "
        "to its credential-delivering response builder; no runtime consumer needed"
    ),
}


def _find_registry_constants(constants_file: Path) -> list[str]:
    """Find all module-level names matching registry/tools/tags/names patterns."""
    tree = ast.parse(constants_file.read_text())
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _REGISTRY_CANONICAL_PATTERN.match(node.target.id):
                names.append(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and _REGISTRY_CANONICAL_PATTERN.match(target.id):
                    names.append(target.id)
    return names


def test_registry_constants_have_production_consumer(
    production_importers: dict[str, set[Path]],
) -> None:
    """Every registry/tools/tags/names constant must be imported by production code or exempted."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    constants_files = [
        src_root / "core" / "types" / "_type_constants_registries.py",
        src_root / "core" / "types" / "_type_recipe_sections.py",
        src_root / "core" / "types" / "_type_constants.py",
    ]

    all_constants: list[tuple[str, Path]] = []
    for cf in constants_files:
        for name in _find_registry_constants(cf):
            all_constants.append((name, cf))

    assert all_constants, f"No registry constants found in {constants_files} — test premise broken"

    unconsumed = []
    for name, def_file in all_constants:
        if name in _REGISTRY_EXEMPTIONS:
            continue
        # When the constants file is the recipe-section authority, also skip
        # the constants facade so we don't double-count identical re-exports
        # from a single facade source. Same logic applies for any future
        # owner→facade pair: find the facade under ``core/types/`` whose
        # module name starts with ``_type_constants_`` and exclude it.
        excluded_files: frozenset[Path] = frozenset()
        if def_file.name == "_type_recipe_sections.py":
            facade_candidate = def_file.parent / "_type_constants_registries.py"
            if facade_candidate.exists():
                excluded_files = frozenset({facade_candidate})
        if not _has_production_import(
            production_importers,
            name,
            def_file,
            excluded_files=excluded_files,
        ):
            unconsumed.append(name)

    assert not unconsumed, (
        f"Registry constants (*_REGISTRY / *_TOOLS / *_TAGS / *_NAMES) with zero "
        f"production consumers and no exemption: {unconsumed}. Each constant must be "
        f"imported by production src/ code or documented in _REGISTRY_EXEMPTIONS "
        f"with a rationale."
    )


def test_exemption_rationales_are_nonempty() -> None:
    """Every exemption must have a non-empty rationale string."""
    empty = [k for k, v in _REGISTRY_EXEMPTIONS.items() if not v.strip()]
    assert not empty, f"Exemptions with empty rationales: {empty}"


def test_exemptions_reference_real_constants() -> None:
    """Every exempted name must exist as a constant in the scanned files."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    constants_files = [
        src_root / "core" / "types" / "_type_constants_registries.py",
        src_root / "core" / "types" / "_type_recipe_sections.py",
        src_root / "core" / "types" / "_type_constants.py",
    ]
    all_names: set[str] = set()
    for cf in constants_files:
        all_names.update(_find_registry_constants(cf))

    stale = set(_REGISTRY_EXEMPTIONS.keys()) - all_names
    assert not stale, (
        f"Exemption dict contains names not found in constants files: {sorted(stale)}. "
        f"Remove stale entries."
    )


def test_fleet_dispatch_tools_subset_of_gated_tools() -> None:
    """FLEET_DISPATCH_TOOLS must be a subset of GATED_TOOLS."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS
    from autoskillit.pipeline import GATED_TOOLS

    extra = FLEET_DISPATCH_TOOLS - GATED_TOOLS
    assert not extra, (
        f"FLEET_DISPATCH_TOOLS must be a subset of GATED_TOOLS — extra: {sorted(extra)}"
    )
