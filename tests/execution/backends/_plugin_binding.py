from __future__ import annotations

from pathlib import Path

from autoskillit.core import PluginArtifactIdentity, PluginLaunchBinding, PluginLoadMode


class _TestLease:
    def __init__(self) -> None:
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True


def plugin_binding(
    plugin_dir: Path | str,
    *,
    inherited_fds: tuple[int, ...] = (),
) -> PluginLaunchBinding:
    path = Path(plugin_dir)
    return PluginLaunchBinding(
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        plugin_dir=path,
        identity=PluginArtifactIdentity(
            semantic_key=f"test:{path}",
            incarnation_id="test-incarnation",
            manifest_schema_version=1,
            artifact_digest="test-digest",
            managed_path=path,
            manifest_path=path.with_name(f"{path.name}.json"),
        ),
        inherited_fds=inherited_fds,
        _lease=_TestLease(),
    )
