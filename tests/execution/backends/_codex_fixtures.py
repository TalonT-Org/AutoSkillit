"""Shared loaders and model catalogs for Codex backend tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

_APP_SERVER_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex_ndjson"


def app_server_fixture(name: str) -> dict[str, Any]:
    return json.loads((_APP_SERVER_FIXTURE_DIR / name).read_text())


def installed_catalog() -> dict[str, object]:
    return {
        "models": [
            {
                "slug": "gpt-6-sol",
                "tool_mode": "code_mode_only",
                "apply_patch_tool_type": "freeform",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Low"},
                    {"effort": "medium", "description": "Medium"},
                    {"effort": "high", "description": "High"},
                    {"effort": "xhigh", "description": "Extra high"},
                    {"effort": "max", "description": "Maximum"},
                    {"effort": "ultra", "description": "Ultra"},
                ],
                "default_reasoning_level": "medium",
                "sentinel": {"preserved": True},
            },
            {
                "slug": "gpt-6-luna",
                "tool_mode": "code_mode_only",
                "apply_patch_tool_type": "freeform",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Low"},
                    {"effort": "medium", "description": "Medium"},
                    {"effort": "high", "description": "High"},
                    {"effort": "xhigh", "description": "Extra high"},
                    {"effort": "max", "description": "Maximum"},
                ],
                "default_reasoning_level": "medium",
                "reader_metadata": {"preserved": True},
            },
        ],
        "metadata": {"catalog": "installed", "schema_version": 7},
    }


def with_migration_offer(catalog: dict[str, object], slug: str, target: str) -> dict[str, object]:
    offered = deepcopy(catalog)
    models = offered["models"]
    assert isinstance(models, list)
    selected = False
    for entry in models:
        assert isinstance(entry, dict)
        if entry.get("slug") == slug:
            entry["upgrade"] = {"model": target, "migration_markdown": f"Meet {target}"}
            selected = True
        elif "upgrade" not in entry:
            entry["upgrade"] = None
    assert selected
    return offered


def managed_source_home(
    tmp_path: Path, *, include_sol: bool = True, catalog: dict[str, object] | None = None
) -> tuple[Path, bytes]:
    source_home = tmp_path / "source"
    source_home.mkdir(parents=True)
    catalog = deepcopy(installed_catalog() if catalog is None else catalog)
    if not include_sol:
        models = catalog["models"]
        assert isinstance(models, list)
        catalog["models"] = [
            model
            for model in models
            if isinstance(model, dict) and model.get("slug") != "gpt-6-sol"
        ]
    raw_catalog = json.dumps(catalog, sort_keys=True).encode("utf-8")
    (source_home / "models_cache.json").write_bytes(raw_catalog)
    return source_home, raw_catalog


def use_bundled_catalog(monkeypatch: pytest.MonkeyPatch, raw_catalog: bytes) -> None:
    from autoskillit.execution.backends import _codex_managed_route

    monkeypatch.setattr(_codex_managed_route.shutil, "which", lambda _binary: "/usr/bin/codex")
    monkeypatch.setattr(
        _codex_managed_route,
        "acquire_bundled_codex_catalog",
        lambda *args, **kwargs: raw_catalog,
    )


def generated_home_snapshot(home: Path) -> dict[str, tuple[int, int, str | None]]:
    snapshot: dict[str, tuple[int, int, str | None]] = {}
    for path in home.rglob("*"):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            content = os.readlink(path)
        elif stat.S_ISREG(info.st_mode):
            content = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            content = None
        snapshot[str(path.relative_to(home))] = (
            stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode),
            content,
        )
    return snapshot
