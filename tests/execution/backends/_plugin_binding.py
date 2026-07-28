from __future__ import annotations

from pathlib import Path

from autoskillit.core import PluginArtifactIdentity, PluginLaunchBinding, PluginLoadMode


class _TestLease:
    def __init__(self, inherited_fds: tuple[int, ...] = ()) -> None:
        self._closed = False
        self._inherited_fds = inherited_fds

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        return self._inherited_fds

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
            incarnation_id="00000000000040008000000000000001",
            manifest_schema_version=1,
            artifact_digest="a" * 64,
            managed_path=path,
            manifest_path=path.with_name(f"{path.name}.json"),
        ),
        inherited_fds=inherited_fds,
        _lease=_TestLease(inherited_fds),
    )
