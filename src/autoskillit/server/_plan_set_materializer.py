"""Server-owned materialization of the content-addressed plan-set authority."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import regex as re

from autoskillit.core import (
    PLAN_SET_AUTHORITY_ID_DOMAIN,
    PLAN_SET_MAX_ISSUE_BYTES,
    PLAN_SET_MAX_PART_BYTES,
    AllocationRowDef,
    CoverageResultDef,
    CoverageStatus,
    GitHubFetcher,
    InventoryMode,
    IssueSnapshotRef,
    PlanPartRef,
    PlanSetAuthority,
    PlanSetBindRequest,
    PlanSetBindResult,
    PlanSetRejectReason,
    PlanSetState,
    RequirementDef,
    atomic_write,
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
    evaluate_coverage,
    extract_requirement_inventory,
    get_logger,
    parse_part_allocation,
    parse_plan_paths,
    read_stable_contained_bytes,
    verify_allocation_evidence,
    verify_plan_set_authority,
    write_canonical_versioned_json,
)

logger = get_logger(__name__)

_PART_SUFFIX = re.compile(r"_part_([a-z0-9]+)", re.IGNORECASE)
_ISSUE_NUMBER = re.compile(r"(?:#|/issues/)(\d+)(?:$|[/?#])")


def _failure(reason: PlanSetRejectReason, error: str) -> PlanSetBindResult:
    return PlanSetBindResult(False, reason, error)


def _write_or_verify(path: Path, data: bytes, allowed_root: Path) -> None:
    try:
        atomic_write(path, data.decode("utf-8"), exclusive=True)
    except FileExistsError:
        _, existing = read_stable_contained_bytes(
            path, allowed_root, max_size_bytes=max(1, len(data))
        )
        if existing != data:
            raise ValueError(f"existing artifact differs: {path}")


def _part_suffix(path: Path, *, only_part: bool) -> str:
    matched = _PART_SUFFIX.search(path.stem)
    if matched:
        return matched.group(1).upper()
    return "A" if only_part else path.stem.upper()


def _coverage_gaps(coverage: CoverageResultDef, requirements: tuple[RequirementDef, ...]) -> str:
    requirement_text = {item.requirement_id: item.text for item in requirements}
    gaps: list[str] = []
    for reason, values in (
        ("unassigned", coverage.unassigned),
        ("uncovered", coverage.uncovered),
        ("duplicate", coverage.duplicate),
        ("unknown", coverage.unknown),
        ("container_allocated", coverage.container_allocated),
    ):
        gaps.extend(
            f"{item} [{reason}]: {requirement_text.get(item, '')}".rstrip() for item in values
        )
    return "\n".join(gaps)


def _result(authority: PlanSetAuthority, path: Path) -> PlanSetBindResult:
    coverage = authority.coverage
    return PlanSetBindResult(
        success=True,
        reason=None,
        error="",
        plan_set_authority_path=str(path),
        plan_set_authority_digest=authority.authority_digest,
        plan_set_authority_id=authority.plan_set_authority_id,
        plan_set_parts="\n".join(part.locator for part in authority.parts),
        plan_set_state=authority.state.value,
        coverage_status=coverage.status.value,
        unassigned=coverage.unassigned,
        uncovered=coverage.uncovered,
        duplicate=coverage.duplicate,
        unknown=coverage.unknown,
        container_allocated=coverage.container_allocated,
        parts_without_obligations=coverage.parts_without_obligations,
        first_part_path=authority.parts[0].locator if authority.parts else "",
        coverage_gaps=_coverage_gaps(coverage, authority.requirements),
    )


class DefaultPlanSetMaterializer:
    """Bind one ordered set of parts into an authority owned by the server."""

    def __init__(self, github_client: GitHubFetcher | None) -> None:
        self._github_client = github_client

    async def bind(self, request: PlanSetBindRequest) -> PlanSetBindResult:
        try:
            return await self._bind(request)
        except Exception as exc:
            logger.error("plan-set binding failed", exc_info=True)
            return _failure(PlanSetRejectReason.INTERNAL, f"{type(exc).__name__}: {exc}")

    async def _bind(self, request: PlanSetBindRequest) -> PlanSetBindResult:
        raw_paths = parse_plan_paths(request.plan_parts_raw)
        if not raw_paths:
            return _failure(PlanSetRejectReason.NO_PARTS, "plan_parts is empty")
        root = request.allowed_root.resolve()
        parent: PlanSetAuthority | None = None
        parent_path: Path | None = None
        if request.parent_authority_path:
            verified = verify_plan_set_authority(
                request.parent_authority_path,
                allowed_root=root,
                expected_execution_generation=request.execution_generation,
                expected_kitchen_id=request.kitchen_id,
                expected_binding_mode=request.binding_mode,
                current_plan_path=None,
                require_sealed=False,
                allow_part_drift=True,
            )
            if not verified.accepted or verified.authority is None:
                return _failure(
                    verified.reason or PlanSetRejectReason.AUTHORITY_INVALID,
                    "; ".join(verified.detail),
                )
            parent = verified.authority
            parent_path = Path(request.parent_authority_path).resolve()

        loaded: list[tuple[Path, bytes]] = []
        try:
            for raw_path in raw_paths:
                loaded.append(
                    read_stable_contained_bytes(
                        raw_path, root, max_size_bytes=PLAN_SET_MAX_PART_BYTES
                    )
                )
        except Exception as exc:
            logger.warning("plan-set part read failed", exc_info=True)
            return _failure(PlanSetRejectReason.CONTAINMENT, str(exc))
        locators = tuple(str(path) for path, _ in loaded)
        if len(set(locators)) != len(locators):
            return _failure(
                PlanSetRejectReason.PART_LIST_CHANGED, "plan parts contain duplicate locators"
            )

        append = False
        renew = False
        parts: list[PlanPartRef] = []
        allocations: list[AllocationRowDef] = []
        issue = None
        issue_body = ""
        revision = 1
        authority_id = ""
        if parent is not None:
            parent_locators = tuple(part.locator for part in parent.parts)
            members = tuple(locator in parent_locators for locator in locators)
            if all(members) and locators == parent_locators:
                renew = True
            elif not any(members) and parent.state is PlanSetState.OPEN:
                append = True
            else:
                return _failure(
                    PlanSetRejectReason.PART_LIST_CHANGED, "parent part list cannot be changed"
                )
            if append:
                unchanged_parent = verify_plan_set_authority(
                    request.parent_authority_path,
                    allowed_root=root,
                    expected_execution_generation=request.execution_generation,
                    expected_kitchen_id=request.kitchen_id,
                    expected_binding_mode=request.binding_mode,
                    current_plan_path=None,
                    require_sealed=False,
                )
                if not unchanged_parent.accepted:
                    return _failure(
                        unchanged_parent.reason or PlanSetRejectReason.AUTHORITY_INVALID,
                        "; ".join(unchanged_parent.detail),
                    )
            parts.extend(parent.parts)
            allocations.extend(parent.allocations)
            issue = parent.issue
            authority_id = parent.plan_set_authority_id
            revision = parent.revision + 1
            if renew and request.seal and parent.state is PlanSetState.SEALED:
                if all(
                    part.byte_size == len(data) and part.content_digest == compute_bytes_hash(data)
                    for part, (_, data) in zip(parent.parts, loaded, strict=True)
                ):
                    return _result(parent, parent_path or Path(request.parent_authority_path))

        if parent is None:
            if request.issue_url:
                if self._github_client is None:
                    return _failure(
                        PlanSetRejectReason.ISSUE_FETCH_FAILED, "GitHub client is unavailable"
                    )
                fetched = await self._github_client.fetch_issue(
                    request.issue_url, include_comments=False
                )
                if not fetched.get("success"):
                    return _failure(
                        PlanSetRejectReason.ISSUE_FETCH_FAILED,
                        str(fetched.get("error", "issue fetch failed")),
                    )
                issue_body = str(fetched.get("body", ""))
                issue_number = fetched.get("issue_number")
                provisional = (
                    "planset-"
                    + compute_canonical_hash(
                        {
                            "execution_generation": request.execution_generation,
                            "kitchen_id": request.kitchen_id,
                            "issue_locator": request.issue_url,
                            "initial_part_locators": list(locators),
                        },
                        domain=PLAN_SET_AUTHORITY_ID_DOMAIN,
                    )[7:31]
                )
                snapshot_root = root / "plan-set-authority" / provisional
                snapshot_root.mkdir(parents=True, exist_ok=True)
                issue_bytes = issue_body.encode("utf-8")
                digest = compute_bytes_hash(issue_bytes)
                snapshot_path = snapshot_root / f"issue.{digest[7:31]}.md"
                _write_or_verify(snapshot_path, issue_bytes, root)
                issue = IssueSnapshotRef(
                    issue_url=request.issue_url,
                    issue_number=issue_number if isinstance(issue_number, int) else None,
                    locator=str(snapshot_path),
                    byte_size=len(issue_bytes),
                    content_digest=digest,
                    fetched_at=datetime.now(UTC).isoformat(),
                )
                authority_id = provisional
            else:
                authority_id = (
                    "planset-"
                    + compute_canonical_hash(
                        {
                            "execution_generation": request.execution_generation,
                            "kitchen_id": request.kitchen_id,
                            "issue_locator": "",
                            "initial_part_locators": list(locators),
                        },
                        domain=PLAN_SET_AUTHORITY_ID_DOMAIN,
                    )[7:31]
                )

        if parent is not None and request.seal and issue is not None and request.issue_url:
            if self._github_client is None:
                return _failure(
                    PlanSetRejectReason.ISSUE_FETCH_FAILED, "GitHub client is unavailable"
                )
            fetched = await self._github_client.fetch_issue(
                request.issue_url, include_comments=False
            )
            if not fetched.get("success"):
                return _failure(
                    PlanSetRejectReason.ISSUE_FETCH_FAILED,
                    str(fetched.get("error", "issue fetch failed")),
                )
            if (
                compute_bytes_hash(str(fetched.get("body", "")).encode("utf-8"))
                != issue.content_digest
            ):
                return _failure(
                    PlanSetRejectReason.ISSUE_DRIFT, "issue body changed since snapshot"
                )

        if renew:
            parts.clear()
            allocations.clear()
        if append or parent is None or renew:
            start = len(parts) + 1
            for ordinal, (path, data) in enumerate(loaded, start=start):
                parts.append(
                    PlanPartRef(
                        ordinal=ordinal,
                        part_key=f"P{ordinal}",
                        part_suffix=_part_suffix(path, only_part=len(loaded) == 1 and not parts),
                        locator=str(path),
                        byte_size=len(data),
                        content_digest=compute_bytes_hash(data),
                    )
                )
        errors: list[str] = []
        loaded_by_locator = {str(path): data for path, data in loaded}
        for part in parts:
            if part.locator not in loaded_by_locator:
                continue
            try:
                text = loaded_by_locator[part.locator].decode("utf-8")
                rows = parse_part_allocation(text, part_key=part.part_key)
            except (UnicodeDecodeError, ValueError) as exc:
                return _failure(PlanSetRejectReason.COVERAGE_FAILED, str(exc))
            allocations.extend(rows)
            errors.extend(
                f"{identifier}: {reason.value}"
                for identifier, reason in verify_allocation_evidence(text, rows)
            )
        if errors:
            return _failure(PlanSetRejectReason.COVERAGE_FAILED, "; ".join(errors))

        requirements: tuple[RequirementDef, ...]
        unparsed: tuple[int, ...]
        if issue is None:
            mode = InventoryMode.NO_ISSUE
            requirements = ()
            unparsed = ()
        else:
            _, issue_bytes = read_stable_contained_bytes(
                issue.locator, root, max_size_bytes=PLAN_SET_MAX_ISSUE_BYTES
            )
            extraction = extract_requirement_inventory(
                issue_bytes.decode("utf-8"), issue_number=issue.issue_number
            )
            mode = extraction.mode
            requirements = extraction.requirements
            unparsed = extraction.unparsed_marker_lines
        if unparsed:
            return _failure(
                PlanSetRejectReason.UNPARSED_MARKERS, f"unparsed marker lines: {unparsed}"
            )
        rows_by_part = {
            part.part_key: tuple(row for row in allocations if row.part_key == part.part_key)
            for part in parts
        }
        coverage = (
            evaluate_coverage(mode, requirements, rows_by_part)
            if request.seal
            else CoverageResultDef(CoverageStatus.NOT_EVALUATED)
        )
        if request.seal and coverage.status is not CoverageStatus.PASS:
            return PlanSetBindResult(
                success=False,
                reason=PlanSetRejectReason.COVERAGE_FAILED,
                error="plan-set coverage has gaps",
                plan_set_authority_path=str(parent_path) if parent_path else "",
                plan_set_authority_digest=parent.authority_digest if parent else "",
                plan_set_authority_id=authority_id,
                plan_set_parts="\n".join(part.locator for part in parent.parts) if parent else "",
                plan_set_state=parent.state.value if parent else "",
                coverage_status=coverage.status.value,
                unassigned=coverage.unassigned,
                uncovered=coverage.uncovered,
                duplicate=coverage.duplicate,
                unknown=coverage.unknown,
                container_allocated=coverage.container_allocated,
                parts_without_obligations=coverage.parts_without_obligations,
                coverage_gaps=_coverage_gaps(coverage, requirements),
            )
        authority = PlanSetAuthority.create(
            binding_mode=request.binding_mode,
            execution_generation=request.execution_generation,
            kitchen_id=request.kitchen_id,
            dispatch_id=request.dispatch_id,
            plan_set_authority_id=authority_id,
            revision=revision,
            parent_authority_digest=parent.authority_digest if parent else None,
            state=PlanSetState.SEALED if request.seal else PlanSetState.OPEN,
            inventory_mode=mode,
            issue=issue,
            requirements=tuple(requirements),
            parts=tuple(parts),
            allocations=tuple(allocations),
            coverage=coverage,
            unparsed_marker_lines=tuple(unparsed),
            generated_at=datetime.now(UTC).isoformat(),
        )
        artifact_dir = root / "plan-set-authority" / authority_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{authority.authority_digest[7:31]}.json"
        try:
            write_canonical_versioned_json(
                artifact_path,
                authority.to_dict(),
                authority.schema_version,
                exclusive=True,
            )
        except FileExistsError:
            _, existing = read_stable_contained_bytes(
                artifact_path, root, max_size_bytes=PLAN_SET_MAX_PART_BYTES
            )
            if existing != canonical_json_bytes(authority.to_dict()):
                return _failure(
                    PlanSetRejectReason.INTERNAL, "authority path contains different bytes"
                )
        return _result(authority, artifact_path)
