"""Atomic write gateway for config.yaml layers.

Owns ``write_config_layer`` — the canonical validate-then-write entrypoint
every config writer must call to land changes on disk. Validation runs FIRST
so the file is never written when its content is rejected; ``atomic_write``
ensures the file is never observed half-written by a concurrent reader.
A module-level ``_WRITE_LOCK`` serializes the dump+write window so two
concurrent callers cannot interleave validate→dump→atomic_write and produce
a lost update.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from autoskillit.config._validation import validate_layer_keys
from autoskillit.core import atomic_write, dump_yaml_str

_WRITE_LOCK = threading.Lock()


def write_config_layer(path: Path, data: dict[str, Any]) -> None:
    """Validate config data against the schema, then atomically write it to path.

    Raises ConfigSchemaError before touching the file if the data contains
    unrecognized keys, unknown sub-keys, or any _SECRETS_ONLY_KEYS entries.
    This is the canonical write gateway for all config.yaml write sites.

    ``path`` must be a non-secrets config.yaml path — never .secrets.yaml,
    which allows different keys.
    """
    validate_layer_keys(data, path, is_secrets_layer=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        atomic_write(path, dump_yaml_str(data, default_flow_style=False, allow_unicode=True))
