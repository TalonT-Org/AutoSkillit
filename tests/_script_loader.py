"""Load standalone scripts with isolated per-test module registration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_script(name: str, path: Path) -> ModuleType:
    """Load one standalone script fresh via importlib, registering it in sys.modules under *name*.

    Each caller passes its own unique *name* so dataclasses defined in the loaded script
    resolve string annotations through sys.modules[cls.__module__] without colliding with a
    sibling test file's copy of the same script (see
    tests/arch/test_acceptance_policy_relaxation_gate.py).

    The script's parent directory is temporarily added to sys.path so sibling imports
    resolve during execution. The caller's sys.path is restored afterward.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    missing = object()
    previous_module = sys.modules.get(name, missing)
    sys.modules[name] = mod
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        if previous_module is missing:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous_module
        raise
    finally:
        sys.path[:] = previous_path
    return mod
