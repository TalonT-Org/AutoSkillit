---
name: audit-arch
description: Audit codebase for adherence to architectural standards, practices, and rules. Use when user says "audit arch", "audit architecture", "check architecture", or "architectural review". Spawns parallel subagents to examine multiple architectural aspects and generates a structured report.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Auditing codebase architecture...'"
          once: true
---

# Architectural Audit Skill

Audit the codebase for adherence to architectural standards and rules.

## When to Use

- User says "audit arch", "audit architecture", "check architecture"

## Critical Constraints

**NEVER:**
- Modify any source code files
- Update an existing report - always generate new

**ALWAYS:**
- Use subagents for parallel exploration
- Write report to `temp/audit-arch/arch_audit_{YYYY-MM-DD_HHMMSS}.md`
- Provide file paths and line numbers
- Categorize by severity (CRITICAL, HIGH, MEDIUM, LOW)

---

## Architectural Principles

### Principle 1: Single Source of Truth

**Rule:** All state reads must come from the authoritative source (database, API, configuration management). File outputs and caches are write-only. Systems never read files back as the primary source of state.

**Audit Strategy - Data Flow Tracing:**

1. **Find all file read operations** in core application code

2. **For each read, trace the data flow:**
   - What data is being read?
   - Does it influence system behavior or state?

3. **Identify the PRIMARY source:**
   - If file is read first and authoritative source synced afterward, file is PRIMARY (CRITICAL violation)
   - If authoritative source is read first, authoritative source is PRIMARY (compliant)

4. **Check write/read symmetry:**
   - Find artifact writes, check for corresponding reads
   - If system reads back what it wrote, that's a violation

**Key Questions:**
- "If this file didn't exist, would the system fail or query the authoritative source?"
- "Does the order of operations show file-first or authoritative-source-first?"

**Critical:** Apply this to ALL state loading code paths. Any component that restores or reconstructs system state must be examined. File-first with authoritative source sync afterward is still file-primary.

**Cross-Reference:** If you discover file-first patterns while auditing other principles, report them here as P1 violations, not just as inconsistencies.

---

### Principle 2: Domain-Based Organization

**Rule:** Clear separation between domains with consistent structure.

**Common patterns:**
- Core domain logic separated from infrastructure
- Clear boundaries between business logic, data access, presentation
- Shared utilities in well-defined locations
- No mixing of concerns (e.g., CLI logic in database layer)

**Audit Strategy:**
- Check for misplaced components (utilities at root, API code in data layer)
- Find orphaned/empty directories from incomplete migrations
- Identify duplicates across locations
- Verify domain boundaries are respected

---

### Principle 3: Dependency Layering

**Rule:** Dependencies flow one direction. Higher layers depend on lower layers, never reverse.

**Typical layering:**
```
presentation/  -> depends on business logic, data access
business logic -> depends on data access, infrastructure
data access    -> depends on infrastructure only
infrastructure -> depends on nothing project-specific
```

**Also check internal layering:** Within a domain, core modules should not import from higher-level modules (handlers, controllers, UI).

**Audit Strategy:**
- Scan imports in each layer for boundary violations
- Look for deferred imports (indicate architectural debt)
- Check that foundational layers don't depend on higher layers
- Verify circular dependencies don't exist

---

### Principle 4: No Cross-Domain Imports

**Rule:** Separate domains/modules must be independent. Feature A cannot import from Feature B directly.

**Audit Strategy:**
- Scan each domain for imports from other domains at the same layer
- Shared functionality should be in common utilities or lower layers
- Check for tight coupling between features

---

### Principle 5: Architecture Pattern Consistency

**Rule:** When using architectural patterns (MVC, repository pattern, state machines, etc.), implementations must follow consistent patterns across the codebase.

**Audit Strategy:**
- Identify the architectural patterns in use
- Compare implementations across different modules
- Check if patterns diverge - is it intentional or inconsistency?
- Look for pattern violations (e.g., bypassing the repository layer)

**Important:** If a component bypasses the established pattern to use file-first state loading, that's a P1 violation - report it under P1, not here.

---

### Principle 6: No Code Duplication

**Rule:** Shared functionality exists in exactly one location.

**Audit Strategy:**
- Find functions/classes with same name in multiple locations
- Check migration pairs: old location should only re-export, not duplicate
- Look for copy-pasted code blocks with slight variations
- Identify logic that could be extracted to shared utilities

**Migration Awareness:** During migration, shims are acceptable only if they re-export from new location. Full duplicate implementations are violations.

---

### Principle 7: Data Access Pattern Compliance

**Rule:** All data access through designated abstraction layer (repositories, DAOs, services), never direct client usage in business logic.

**Audit Strategy:**
- Find direct database/API client usage outside designated data access layer
- Check for direct imports of database drivers, HTTP clients in business logic
- Verify all queries go through the abstraction layer

---

### Principle 8: No Monolithic Files

**Rule:** No file should exceed 1000 lines. Large files should be decomposed.

**Audit Strategy:**
- Find files exceeding 1000 lines (exclude generated/vendored)
- Flag files approaching threshold (800+ lines) as warnings

---

### Principle 9: Model Construction Integrity

**Rule:** When constructing models/objects from dicts/external data, use factory methods or full validation. Never manually select fields in constructor calls.

**Rationale:** Manual field selection silently drops unlisted fields. Optional fields are especially vulnerable since missing them causes no validation error.

**Audit Strategy:**
- Find `Model(field1=dict["x"], field2=dict.get("y"))` patterns
- Check if all source dict fields are mapped to target model
- Verify factory methods exist for cross-schema transformations
- Look for validation being skipped

**Severity:** HIGH - silent data loss breaks downstream consumers

---

### Principle 10: External Interface Compliance

**Rule:** Classes extending external framework base classes must implement ALL interface methods explicitly. Avoid mixin patterns where method resolution order affects behavior.

**Audit Strategy:**
- Find classes extending external bases (framework classes, third-party libraries)
- Check mixin ordering: mixins should come BEFORE the base class they augment
- Verify both sync AND async methods work (not inherited `NotImplementedError` stubs)
- Confirm contract tests exist for external interface compliance

**Severity:** CRITICAL - Interface mismatches only surface at runtime in specific code paths

---

### Principle 11: Dependency Currency

**Rule:** Direct dependencies should track current major versions. Minor/patch drift is acceptable; lagging a major version is not.

**Audit Strategy:**
- Compare installed major versions against current stable releases for key dependencies
- Flag any dependency more than one major version behind

**Severity:** MEDIUM - stale major versions accumulate migration debt and miss security fixes

---

### Principle 12: Protocol Contracts and Composition Root

**Rule:** All service dependencies injected into the central DI container (`ToolContext`) must be expressed as `@runtime_checkable Protocol` types defined in `core/types.py`. Each Protocol must have exactly one concrete `Default*` adapter in its domain layer. The Composition Root (`server/_factory.py`) is the only location in production code that may instantiate all concrete services simultaneously and wire them into `ToolContext`. All `ToolContext` field annotations must use Protocol types, never concrete classes.

**Rationale:** Protocol-typed fields allow test substitution without import pollution. A single wiring point makes the full dependency graph explicit and auditable. Naming convention (`Default*`) signals the standard production implementation and distinguishes it from test doubles. Concrete class leakage into field annotations couples the container to implementations rather than contracts.

**Audit Strategy:**
- Scan `core/types.py` for all `@runtime_checkable Protocol` definitions
- For each Protocol, verify exactly one `Default*` concrete adapter exists in the codebase
- Check all concrete implementations follow the `Default*` naming convention (e.g., `DefaultTestRunner`, not `RealTestRunner` or `TestRunnerImpl`)
- Verify `ToolContext` field annotations use only Protocol types (not concrete classes)
- Confirm `ToolContext` is only instantiated in `server/_factory.py` and test files
- Flag any `Default*` adapter instantiated outside the Composition Root in production code

**Severity:** HIGH — concrete class leakage in field annotations defeats substitutability; Protocol naming gaps make the DI contract non-discoverable

---

### Principle 13: AST-Based Architectural Rule Enforcement

**Rule:** Architectural constraints that can be expressed as properties of source code structure — rather than runtime behavior — must be encoded as AST-parsed rules in `tests/test_architecture.py`. Each rule must carry: a unique `rule_id`, a `defense_standard` reference, a human-readable `rationale`, and an explicit named `exemptions` list. Exemptions must be file-specific (not pattern-matched) and minimized. Any new architectural commitment discovered during an audit must be translated into an AST rule before the PR merges.

**Rationale:** Runtime tests only catch violations on executed code paths. AST rules catch structural violations at `pytest` time across every source file with zero execution overhead. They scale automatically as new files are added, without requiring test updates. The rule registry self-documents the project's structural invariants. Gaps in AST coverage — constraints known but unenforced — represent architectural debt that accumulates silently.

**Audit Strategy:**
- Read `tests/test_architecture.py` and enumerate all currently enforced AST rules
- For each architectural principle in this skill, ask: "Is there a corresponding AST rule?"
- Check for deferred-import bypass gaps: does the layer enforcement test scan function bodies or only module-level statements?
- Identify architectural constraints described in CLAUDE.md or doc comments that have no corresponding test
- Flag any exemption that is a glob pattern rather than an explicit named file
- Check that the `RuleDescriptor` dataclass (or equivalent) is frozen and fields are complete for all rules

**Severity:** MEDIUM for gaps in existing enforcement; HIGH for any newly agreed architectural rule with no enforcement at all

---

### Principle 14: Gateway API — Package Facade Isolation

**Rule:** All imports of symbols from another sub-package must go through that sub-package's `__init__.py` gateway — never through a submodule path directly (e.g., `from autoskillit.recipe.validator import X` is forbidden outside `recipe/`; use `from autoskillit.recipe import X`). Sub-package `__init__.py` files are the public API contract: their `__all__` is the surface. Submodule paths are internal implementation details free to be restructured. Facade functions in `__init__.py` that require deferred function-body imports (to work around circular initialization) must instead be extracted to a dedicated `_api.py` submodule and re-exported from `__init__.py`.

**Rationale:** Submodule-path imports create tight coupling to internal structure, making refactoring unsafe. The gateway pattern isolates external consumers from internal reorganization. When `__init__.py` contains substantive logic that requires deferred imports to avoid circular initialization, it signals that the function belongs in a real submodule — `__init__.py` should be a pure re-export facade. The `_api.py` convention provides a home for cross-cutting orchestration that needs imports from multiple internal submodules.

**Audit Strategy:**
- Search for `from autoskillit.X.submodule import` patterns in files outside package `X`
- Verify all sub-package `__init__.py` files declare a complete `__all__` matching their re-exports
- Look for substantive function definitions (not just `=` aliases or re-imports) inside `__init__.py` files
- For each such function in `__init__.py`, check if it uses deferred (function-body) imports — if yes, flag for extraction to `_api.py`
- Check that no `__init__.py` function body contains 3+ deferred imports (strong signal of misplacement)
- Verify a `test_no_cross_package_submodule_imports` (or equivalent) AST test exists and covers all sub-packages

**Severity:** MEDIUM for submodule-path leakage; MEDIUM for deferred-import-heavy `__init__.py` functions (circular import debt indicator)

---

## Cross-Cutting Design Guidelines

These apply across all principles when evaluating architectural decisions:

1. **Implicit correction masks upstream failures** — Reject invalid input rather than fixing it. Examples: silent type conversion, default values for required fields, translation layers that never reject, retry loops that swallow errors.

2. **Functions that accept all inputs without rejection are fallbacks, not validators** — If a "validator" or "normalizer" never raises an error, it's hiding problems.

3. **System-derived values belong in code, not external input** — Values determined by workflow state (status, IDs, counts) should be set by the system that owns them, not expected from external sources.

4. **No backward compatibility** — Flag any code containing these keywords as violations: `legacy`, `deprecated`, `backward`, `compat`, `migration shim`, `old format`, `previous version`, `for compatibility`. Dead code should be deleted, not preserved with comments explaining why it exists.

---

## Audit Workflow

1. **Launch parallel subagents** for each principle
2. **Consolidate findings** by principle and severity
3. **Cross-reference:** Ensure findings are categorized by the principle they violate, not just where discovered
4. **Suggest new principle** (optional) - see below
5. **Write report** to `temp/audit-arch/arch_audit_{YYYY-MM-DD_HHMMSS}.md`
6. **Output summary** to terminal

---

## Principle Suggestion (Optional)

After consolidating findings, consider whether a **new** architectural principle would significantly benefit the codebase.

**Criteria - ALL must be true:**
- Not a one-off issue
- No existing principle covers it
- Would prevent recurring architectural debt or bugs
- Impact would be HIGH or CRITICAL level

**If criteria met:** Add "Suggested Principle" section to report with:
- One-sentence rule statement
- 2-3 specific locations that motivated it

**If criteria NOT met:** Omit section entirely. Do not suggest principles just to have a suggestion.

---

## Exclusions

Do NOT flag:
- Test files
- Re-export shims (thin wrappers only)
- Project config reads (package.json, build configs)
- External tool output (test runner output, build logs)

---

## Severity Guidelines

**CRITICAL:**
- Reading state from secondary sources instead of authoritative source
- Circular dependencies between domains
- External interface contract violations

**HIGH:**
- Lower layers importing from higher layers
- Cross-domain imports at same layer
- Duplicate implementations
- Manual field selection causing silent data loss

**MEDIUM:**
- Code in wrong domain
- Inconsistent patterns
- Deferred imports indicating debt
- Stale major version dependencies

**LOW:**
- Naming inconsistencies
- Empty directories not cleaned up
