# install/

Install cluster: market transactions, plugin-artifact authority, install-type detection.

## Architecture Notes

`__init__.py` is a PEP 562 lazy re-export facade — `__getattr__` resolves each
public name on first access so the heavy install-cluster import graph
(workspace, hooks, install_snapshot, marketplace) does not execute at module
load. The leaf `_install_info` is imported function-locally at all call sites
in `cli/doctor/` and `cli/update/`, preserving the original cycle-free import
boundary.
