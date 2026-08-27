"""Skill migration adapter — deterministic frontmatter-only migration for stale skills."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS,
    SKILL_CONTRACT_REMEDIATIONS,
    SKILL_SEMANTIC_SCHEMA_VERSION,
    RemediationAction,
    SkillContractError,
    SkillInvalidityKind,
    dump_yaml_str,
)
from autoskillit.migration.engine import (
    DeterministicMigrationAdapter,
    MigrationFile,
    MigrationResult,
    _normalize_legacy_child_spawn_cardinality,
    _skill_project_dir,
)

if TYPE_CHECKING:
    from autoskillit.workspace import SkillInfo


class SkillMigrationAdapter(DeterministicMigrationAdapter):
    """Deterministic adapter for repairing skill frontmatter in stale skills."""

    file_type = "skill"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        files: list[MigrationFile] = []
        for search_dir in ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS:
            search_root = project_dir / search_dir
            if not search_root.is_dir():
                continue
            for entry in sorted(search_root.iterdir(), key=lambda item: item.name):
                skill_md = entry / "SKILL.md"
                if entry.is_dir() and not entry.is_symlink() and skill_md.is_file():
                    files.append(
                        MigrationFile(
                            name=entry.name,
                            path=skill_md,
                            file_type=self.file_type,
                            current_version=None,
                        )
                    )
        return files

    def _resolve_candidate(self, file: MigrationFile) -> SkillInfo | None:
        # The raw-candidate accessor from the resolution-boundary containment
        # step: resolve_effective() would fall through to a valid bundled
        # twin (or a valid lower-precedence local copy) and validate that
        # instead of the stale file this adapter was asked to fix.
        from autoskillit.workspace import default_skill_resolver  # noqa: PLC0415

        project_dir = _skill_project_dir(file.path)
        return default_skill_resolver().resolve_local_candidate(file.name, project_dir)

    def needs_migration(self, file: MigrationFile) -> bool:
        info = self._resolve_candidate(file)
        if info is None or not info.invalidities:
            return False
        return any(
            SKILL_CONTRACT_REMEDIATIONS[item.kind].action is RemediationAction.DETERMINISTIC
            for item in info.invalidities
        )

    async def migrate(self, file: MigrationFile, *, temp_dir: Path) -> MigrationResult:
        info = self._resolve_candidate(file)
        if info is None or not info.invalidities:
            return MigrationResult(success=True, name=file.name)

        applicable_kinds = tuple(
            dict.fromkeys(
                item.kind
                for item in info.invalidities
                if SKILL_CONTRACT_REMEDIATIONS[item.kind].action is RemediationAction.DETERMINISTIC
            )
        )
        if not applicable_kinds:
            remaining = sorted({item.kind.value for item in info.invalidities})
            return MigrationResult(
                success=False,
                name=file.name,
                error=f"no deterministic remediation registered for: {remaining}",
            )

        parsed = info.frontmatter
        if parsed is None or parsed.data is None:
            return MigrationResult(
                success=False, name=file.name, error="frontmatter did not parse"
            )
        data = dict(parsed.data)
        declared_caps_raw = data.get("uses_capabilities", [])
        if not isinstance(declared_caps_raw, list):
            return MigrationResult(
                success=False,
                name=file.name,
                error="uses_capabilities must be a list before deterministic migration",
            )
        declared_caps = {str(capability) for capability in declared_caps_raw}

        for kind in applicable_kinds:
            if kind is SkillInvalidityKind.UNDECLARED_CAPABILITY:
                missing: set[str] = set()
                for item in info.invalidities:
                    if item.kind is kind:
                        if item.capability is None:
                            return MigrationResult(
                                success=False,
                                name=file.name,
                                error="undeclared capability invalidity has no typed capability",
                            )
                        missing.add(item.capability)
                declared_caps.update(missing)
                data["uses_capabilities"] = sorted(declared_caps)
            elif kind is SkillInvalidityKind.SEMANTIC_MISSING_VERSION:
                data["semantic_version"] = SKILL_SEMANTIC_SCHEMA_VERSION
            elif kind is SkillInvalidityKind.SEMANTIC_UNDECLARED_TOKENS:
                # Only the retired-capability half of this kind is repairable
                # without touching the body: dropping a retired name from
                # uses_capabilities (a frontmatter field) and declaring its
                # replacement stops it from being flagged again. A raw
                # portable token (Agent(, subagent_type=, ...) literally
                # present in the body cannot be fixed frontmatter-only —
                # this adapter never rewrites body prose. If no declared
                # retired capability triggered this kind, the only possible
                # cause is such a raw body token, so report failure instead
                # of silently claiming a fix that never happened.
                from autoskillit.workspace import (  # noqa: PLC0415
                    RETIRED_SEMANTIC_CAPABILITIES,
                )

                retired = declared_caps & RETIRED_SEMANTIC_CAPABILITIES.keys()
                if not retired:
                    return MigrationResult(
                        success=False,
                        name=file.name,
                        error=(
                            "raw portable token(s) in skill body cannot be fixed "
                            "frontmatter-only; rewrite the body to remove the "
                            "offending token(s), or leave this finding as an "
                            "operator-visible advisory"
                        ),
                    )
                declared_caps.difference_update(retired)
                data["uses_capabilities"] = sorted(declared_caps)
                data.setdefault("semantic_version", SKILL_SEMANTIC_SCHEMA_VERSION)
                requirements = dict(data.get("semantic_requirements") or {})
                for capability in retired:
                    field = RETIRED_SEMANTIC_CAPABILITIES[capability].rsplit(".", 1)[-1]
                    requirements.setdefault(field, [])
                data["semantic_requirements"] = requirements
            elif kind is SkillInvalidityKind.SEMANTIC_CHILD_CARDINALITY_INVALID:
                migration_error = _normalize_legacy_child_spawn_cardinality(data)
                if migration_error is not None:
                    return MigrationResult(
                        success=False,
                        name=file.name,
                        error=migration_error,
                    )
            else:
                raise SkillContractError(
                    f"SkillMigrationAdapter has no migration for invalidity kind {kind.value!r}"
                )

        new_frontmatter = dump_yaml_str(data).rstrip("\n")
        migrated_content = f"---\n{new_frontmatter}\n---\n{parsed.body}"
        return MigrationResult(success=True, name=file.name, migrated_content=migrated_content)

    def validate(self, path: Path) -> tuple[bool, str]:
        from autoskillit.workspace import (  # noqa: PLC0415
            default_skill_resolver,
            read_skill_frontmatter,
        )

        parsed = read_skill_frontmatter(path)
        if not parsed.is_valid:
            return False, f"frontmatter still invalid: {parsed.error}"
        info = default_skill_resolver().resolve_local_candidate(
            path.parent.name,
            _skill_project_dir(path),
        )
        if info is None:
            return False, "migrated skill candidate could not be resolved"
        remaining = tuple(
            item
            for item in info.invalidities
            if SKILL_CONTRACT_REMEDIATIONS[item.kind].action is RemediationAction.DETERMINISTIC
        )
        if remaining:
            details = "; ".join(item.detail for item in remaining)
            return False, f"deterministic skill invalidities remain: {details}"
        return True, ""

    def post_migration_validate(self, path: Path) -> tuple[bool, str] | None:
        """Run the typed skill re-validation after the engine writes content back."""
        return self.validate(path)
