"""Dynaconf layer loading and config resolution for AutomationConfig."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.config._config_dataclasses import PacksConfig, SubsetsConfig
from autoskillit.core import (
    CATEGORY_TAGS,
    dump_yaml_str,
    get_logger,
    load_yaml,
    pkg_root,
)

if TYPE_CHECKING:
    from dynaconf import Dynaconf

    from autoskillit.config.settings import AutomationConfig

logger = get_logger(__name__)


def _build_subsets_config(raw: dict[str, Any]) -> SubsetsConfig:
    """Parse subsets section, emitting warnings for unknown disabled categories."""
    disabled = list(raw.get("disabled", []))
    custom_tags_raw = raw.get("custom_tags", {}) or {}
    if not isinstance(custom_tags_raw, dict):
        raise ValueError(
            f"subsets.custom_tags must be a dict mapping tag names to skill lists, "
            f"got {type(custom_tags_raw).__name__!r}: {custom_tags_raw!r}"
        )
    custom_tags: dict[str, list[str]] = {}
    for k, v in custom_tags_raw.items():
        if isinstance(v, list):
            custom_tags[str(k)] = [str(item) for item in v]
        else:
            logger.warning(
                "Ignoring non-list value for custom_tags entry %r: %r",
                k,
                v,
            )
    known_categories = CATEGORY_TAGS | frozenset(custom_tags.keys())
    for tag in disabled:
        if tag not in known_categories:
            logger.warning(
                "Unknown category %r in subsets.disabled"
                " (not in CATEGORY_TAGS and not a custom_tag)",
                tag,
            )
    return SubsetsConfig(disabled=disabled, custom_tags=custom_tags)


def _build_packs_config(raw: dict[str, Any]) -> PacksConfig:
    """Parse packs section, warning on unknown pack names."""
    from autoskillit.core import PACK_REGISTRY

    enabled = list(raw.get("enabled", []))
    for pack_name in enabled:
        if pack_name not in PACK_REGISTRY:
            logger.warning(
                "Unknown pack name %r in packs.enabled (not in PACK_REGISTRY)",
                pack_name,
            )
    return PacksConfig(enabled=enabled)


def _to_optional_list(value: Any) -> list[str] | None:
    """Return None if value is falsy, else coerce to list[str]."""
    if not value:
        return None
    return list(value)


def _to_optional_commands(value: Any) -> list[list[str]] | None:
    """Return None if value is falsy, else coerce to list[list[str]]."""
    from autoskillit.config._config_dataclasses import ConfigSchemaError

    if not value:
        return None
    if not isinstance(value, list) or any(not isinstance(cmd, (list, tuple)) for cmd in value):
        raise ConfigSchemaError(f"test_check.commands must be a list of lists, got: {value!r}")
    return [list(cmd) for cmd in value]


def _apply_layer(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Apply override into base with dict deep-merge and list-replace semantics.

    Dicts are recursively merged so that a partial section in a later layer
    (e.g. project config with only github.default_repo) does not wipe sibling
    keys set by an earlier layer (e.g. user config with github.token).
    All other value types — including lists — are replaced outright, preserving
    the intuitive expectation that setting test_check.command in a config file
    gives exactly that command (not the defaults appended to it).
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _apply_layer(base[key], value)
        else:
            base[key] = value


def _merge_yaml_layers(*paths: Path) -> dict[str, Any]:
    """Load and merge YAML files in order, applying _apply_layer for each."""
    result: dict[str, Any] = {}
    for path in paths:
        if path.is_file():
            data = load_yaml(path)
            if isinstance(data, dict):
                _apply_layer(result, data)
    return result


def _make_dynaconf(project_dir: Path | None = None) -> Dynaconf:
    """Create a Dynaconf instance for env-var overrides over pre-merged file layers.

    File layers (defaults, user, project, secrets) are merged in advance with
    dict deep-merge + list-replace semantics. User-writable layers are validated
    for unrecognized keys before merging. The merged result is written to a temp
    YAML file so that Dynaconf can apply env var overrides (AUTOSKILLIT_SECTION__KEY).

    Deferred import keeps dynaconf off the module-level import chain.
    """
    from dynaconf import Dynaconf  # noqa: PLC0415

    from autoskillit.config._config_dataclasses import ConfigSchemaError
    from autoskillit.config.settings import validate_layer_keys

    defaults_path = pkg_root() / "config" / "defaults.yaml"
    root = project_dir or Path.cwd()

    # Layer definitions: (path, should_validate, is_secrets_layer)
    _layers = [
        (defaults_path, False, False),
        (Path.home() / ".autoskillit" / "config.yaml", True, False),
        (root / ".autoskillit" / "config.yaml", True, False),
        (root / ".autoskillit" / ".secrets.yaml", True, True),
    ]

    merged: dict[str, Any] = {}
    for path, should_validate, is_secrets in _layers:
        if path.is_file():
            data = load_yaml(path)
            if isinstance(data, dict):
                if should_validate:
                    validate_layer_keys(data, path, is_secrets_layer=is_secrets)
                _apply_layer(merged, data)
            elif data is not None:
                raise ConfigSchemaError(
                    f"Invalid configuration in {str(path)!r}: "
                    f"expected a YAML mapping at the top level, "
                    f"got {type(data).__name__!r}."
                )

    # Write to a temp file so Dynaconf can load it and apply env var overrides.
    # Dynaconf reads files lazily; we trigger eager loading before the file is
    # deleted so the in-memory cache remains valid.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write(dump_yaml_str(merged))
        tmp_path = Path(tmp.name)

    try:
        d = Dynaconf(
            envvar_prefix="AUTOSKILLIT",
            preload=[str(tmp_path)],
            settings_files=[],
            merge_enabled=False,
            load_dotenv=False,
            environments=False,
        )
        d.as_dict()  # trigger eager load so the temp file can be safely deleted
    finally:
        tmp_path.unlink(missing_ok=True)

    return d


def load_config(project_dir: Path | None = None) -> AutomationConfig:
    """Load layered config: defaults < user < project < secrets < env vars."""
    from autoskillit.config.settings import AutomationConfig

    return AutomationConfig.from_dynaconf(_make_dynaconf(project_dir))
