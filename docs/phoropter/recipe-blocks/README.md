# Recipe Blocks

Reusable YAML recipe fragments demonstrating phoropter step configuration patterns. Each file contains annotated step blocks that can be adapted for new recipes. See [execution-contract.md](../execution-contract.md) for the contracts these patterns satisfy.

| File | Pattern | When to Use |
|------|---------|-------------|
| [single-family-canonical.yaml](single-family-canonical.yaml) | Canonical step keys with `phoropter_family` annotation | Recipe contains a single phoropter family (Case A) |
| [multi-family-prefixed.yaml](multi-family-prefixed.yaml) | Prefixed step keys without `phoropter_family` on secondary families | Recipe contains two or more coexisting phoropter families (Case B) |
| [null-synthesis-config.yaml](null-synthesis-config.yaml) | Null synthesis via `phoropter-null-synthesis` | Family uses identity pass-through aggregation (arch-lens) |
| [priority-synthesis-config.yaml](priority-synthesis-config.yaml) | Priority hierarchy via `phoropter-priority-synthesis` | Family uses configurable priority-based conflict resolution (vis-lens) |
