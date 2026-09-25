#!/usr/bin/env bash
# Args: snapshot OUTPUT_DIR CHECKOUT_ROOT MODE PR_NUMBER METRICS ANNOTATED_DIFF HUNK_RANGES VALID_LINES
#       revalidate AUTHORITY_PATH
set -uo pipefail

snapshot() {
    local output_dir="$1" checkout_root="$2" mode="$3" pr_number="$4"
    local diff_metrics_path="$5" annotated_diff_path="$6"
    local hunk_ranges_path="$7" valid_lines_path="$8"
    local snapshot_dir authority_path authority_tmp
    if [[ "$output_dir" != /* || ! -d "$output_dir" ]]; then
        printf 'snapshot requires an existing absolute output directory\n' >&2
        return 2
    fi
    if [[ "$mode" != local && "$mode" != github ]]; then
        printf 'snapshot mode must be local or github\n' >&2
        return 2
    fi
    output_dir="$(cd "$output_dir" && pwd -P)" || return 2
    snapshot_dir="$(mktemp -d "$output_dir/gate_snapshot.XXXXXX")" || return 2
    authority_path="$snapshot_dir/gate_authority.json"
    REVIEW_CHECKOUT_ROOT="$checkout_root"
    REVIEW_OUTPUT_DIR="$output_dir"
    MODE="$mode"
    if [[ -d "$checkout_root" ]]; then
        cd "$checkout_root" || return 2
    fi
METRICS_HEAD_SHA=""
METRICS_BASE_SHA=""
METRICS_MERGE_BASE_SHA=""
METRICS_BASE_REPO_FULL_NAME=""
CHECKOUT_HEAD_SHA=""
CHECKOUT_BASE_SHA=""
CHECKOUT_MERGE_BASE_SHA=""
LIVE_REFS=""
LIVE_HEAD_SHA=""
LIVE_BASE_SHA=""
LIVE_BASE_REPO_FULL_NAME=""
LIVE_MERGE_BASE_SHA=""
DIFF_SHA256=""
PROFILE_ID=""
ANNOTATION_GENERATION_ID=""
METRICS_MARKER_BEFORE="$snapshot_dir/metrics.before"
METRICS_MARKER_AFTER="$snapshot_dir/metrics.after"
ARTIFACT_SNAPSHOT_DIR=""
ANNOTATED_DIFF_SNAPSHOT_PATH="$snapshot_dir/annotated_diff"
HUNK_RANGES_SNAPSHOT_PATH="$snapshot_dir/hunk_ranges"
VALID_LINES_SNAPSHOT_PATH="$snapshot_dir/valid_lines"
ANNOTATED_DIFF=""
VALID_LINE_RANGES="{}"
VALID_DIFF_LINES=""
GATE_STATE=degraded
GATE_REASON_CODE=metrics_missing
GATE_FAILED=false
EXPERIMENTAL_AUDIT_STATE=not_eligible

# Closed degraded reason codes:
# metrics_missing, metrics_invalid_json, manifest_missing, manifest_invalid,
# profile_invalid, ref_missing, snapshot_mismatch, artifact_missing,
# artifact_name_mismatch, artifact_length_mismatch, artifact_digest_mismatch,
# marker_changed, gate_missing, gate_not_boolean.
degrade_gate() {
    if [ "$GATE_FAILED" = false ]; then
        GATE_STATE=degraded
        GATE_REASON_CODE="$1"
        GATE_FAILED=true
    fi
    ANNOTATED_DIFF=""
    VALID_LINE_RANGES="{}"
    VALID_DIFF_LINES=""
}

if [ -z "$REVIEW_CHECKOUT_ROOT" ] || [ ! -d "$REVIEW_CHECKOUT_ROOT" ]; then
    degrade_gate ref_missing
elif [ -z "${diff_metrics_path:-}" ] || [ ! -f "$diff_metrics_path" ]; then
    degrade_gate metrics_missing
else
    # Retain one candidate generation in invocation-scoped files. Each cp reads
    # through one open descriptor, so atomic publisher replacement can select an
    # old or new complete file but cannot create mixed bytes within a retained file.
    ARTIFACT_SNAPSHOT_DIR="$snapshot_dir"
    METRICS_MARKER_BEFORE="${ARTIFACT_SNAPSHOT_DIR}/metrics.before"
    METRICS_MARKER_AFTER="${ARTIFACT_SNAPSHOT_DIR}/metrics.after"
    if [ "$GATE_FAILED" = false ] &&
       ! cp -- "$diff_metrics_path" "$METRICS_MARKER_BEFORE"; then
        degrade_gate metrics_missing
    fi

    if [ "$GATE_FAILED" = false ] &&
       ! jq -e 'type == "object"' < "$METRICS_MARKER_BEFORE" >/dev/null; then
        degrade_gate metrics_invalid_json
    elif [ "$GATE_FAILED" = false ] &&
         ! jq -e '
        has("_head_sha") and
        has("_base_sha") and
        has("_base_repo_full_name") and
        has("generation_id") and
        has("diff_sha256") and
        has("diff_byte_length") and
        has("diff_source") and
        has("artifacts") and
        (.artifacts | has("annotated_diff") and has("hunk_ranges") and has("valid_lines"))
    ' < "$METRICS_MARKER_BEFORE" >/dev/null; then
        degrade_gate manifest_missing
    elif [ "$GATE_FAILED" = false ] &&
         ! jq -e '
        (._head_sha | type == "string" and length > 0) and
        (._base_sha | type == "string" and length > 0) and
        (._base_repo_full_name | type == "string") and
        (.generation_id | type == "string" and length > 0) and
        (.diff_sha256 | type == "string" and length == 64) and
        (.diff_byte_length | type == "number" and . >= 0 and floor == .) and
        (.diff_source | type == "object") and
        (.artifacts | type == "object") and
        (.artifacts.annotated_diff | type == "object") and
        (.artifacts.hunk_ranges | type == "object") and
        (.artifacts.valid_lines | type == "object")
    ' < "$METRICS_MARKER_BEFORE" >/dev/null; then
        degrade_gate manifest_invalid
    fi

    if [ "$GATE_FAILED" = false ]; then
        METRICS_HEAD_SHA="$(jq -r '._head_sha' < "$METRICS_MARKER_BEFORE")"
        METRICS_BASE_SHA="$(jq -r '._base_sha' < "$METRICS_MARKER_BEFORE")"
        METRICS_MERGE_BASE_SHA="$(jq -r '._merge_base_sha // ""' < "$METRICS_MARKER_BEFORE")"
        METRICS_BASE_REPO_FULL_NAME="$(jq -r '._base_repo_full_name // ""' < "$METRICS_MARKER_BEFORE")"
        ANNOTATION_GENERATION_ID="$(jq -r '.generation_id' < "$METRICS_MARKER_BEFORE")"
        DIFF_SHA256="$(jq -r '.diff_sha256' < "$METRICS_MARKER_BEFORE")"
        PROFILE_ID="$(jq -r '.diff_source.profile_id // ""' < "$METRICS_MARKER_BEFORE")"

        # Validate the closed source/profile object before any gate read.
        if [ "$MODE" = "local" ]; then
            jq -e '
              .review_mode == "local" and .diff_source == {
                "comparison":"merge_base_to_head","context_lines":3,
                "external_diff":false,"kind":"local_git",
                "profile_id":"local_git_pinned_v1","rename_detection":"50%",
                "text_conversion":false
              }' < "$METRICS_MARKER_BEFORE" >/dev/null || degrade_gate profile_invalid
        else
            jq -e '
              .review_mode == "github" and .diff_source == {
                "comparison":"pull_request","context_lines":3,
                "external_diff":false,"kind":"github_pr",
                "profile_id":"github_pr_diff_v1","rename_detection":"provider_default",
                "text_conversion":false
              }' < "$METRICS_MARKER_BEFORE" >/dev/null || degrade_gate profile_invalid
        fi

        CHECKOUT_HEAD_SHA="$(git -C "$REVIEW_CHECKOUT_ROOT" rev-parse HEAD 2>/dev/null || true)"
        if [ -z "$CHECKOUT_HEAD_SHA" ] || [ "$CHECKOUT_HEAD_SHA" != "$METRICS_HEAD_SHA" ]; then
            degrade_gate snapshot_mismatch
        elif [ "$MODE" = "local" ]; then
            LIVE_REFS="$(
              gh api "repos/{owner}/{repo}/pulls/${pr_number}" \
                --jq '{headRefOid:.head.sha,baseRefOid:.base.sha,baseRepoFullName:.base.repo.full_name}' 2>/dev/null || true
            )"
            LIVE_HEAD_SHA="$(printf '%s' "$LIVE_REFS" | jq -r '.headRefOid // ""' 2>/dev/null)"
            LIVE_BASE_SHA="$(printf '%s' "$LIVE_REFS" | jq -r '.baseRefOid // ""' 2>/dev/null)"
            LIVE_BASE_REPO_FULL_NAME="$(printf '%s' "$LIVE_REFS" | jq -r '.baseRepoFullName // ""' 2>/dev/null)"
            LIVE_MERGE_BASE_SHA="$(gh api \
              "repos/${LIVE_BASE_REPO_FULL_NAME}/compare/${LIVE_BASE_SHA}...${LIVE_HEAD_SHA}" \
              --jq '.merge_base_commit.sha' 2>/dev/null || true)"
            CHECKOUT_MERGE_BASE_SHA="$(git -C "$REVIEW_CHECKOUT_ROOT" merge-base "$METRICS_BASE_SHA" "$CHECKOUT_HEAD_SHA" 2>/dev/null || true)"
            if [ -z "$LIVE_HEAD_SHA" ] || [ -z "$LIVE_BASE_SHA" ] ||
               [ -z "$LIVE_BASE_REPO_FULL_NAME" ] || [ -z "$LIVE_MERGE_BASE_SHA" ] ||
               [ -z "$CHECKOUT_MERGE_BASE_SHA" ]; then
                degrade_gate ref_missing
            elif [ "$LIVE_HEAD_SHA" != "$METRICS_HEAD_SHA" ] ||
                 [ "$LIVE_BASE_SHA" != "$METRICS_BASE_SHA" ] ||
                 [ "$LIVE_BASE_REPO_FULL_NAME" != "$METRICS_BASE_REPO_FULL_NAME" ] ||
                 [ "$LIVE_MERGE_BASE_SHA" != "$METRICS_MERGE_BASE_SHA" ] ||
                 [ "$CHECKOUT_MERGE_BASE_SHA" != "$METRICS_MERGE_BASE_SHA" ]; then
                degrade_gate snapshot_mismatch
            fi
        else
            LIVE_REFS="$(
              gh api "repos/{owner}/{repo}/pulls/${pr_number}" \
                --jq '{headRefOid:.head.sha,baseRefOid:.base.sha}' 2>/dev/null || true
            )"
            LIVE_HEAD_SHA="$(printf '%s' "$LIVE_REFS" | jq -r '.headRefOid // ""' 2>/dev/null)"
            LIVE_BASE_SHA="$(printf '%s' "$LIVE_REFS" | jq -r '.baseRefOid // ""' 2>/dev/null)"
            if [ -z "$LIVE_HEAD_SHA" ] || [ -z "$LIVE_BASE_SHA" ]; then
                degrade_gate ref_missing
            elif [ "$LIVE_HEAD_SHA" != "$METRICS_HEAD_SHA" ] ||
                 [ "$LIVE_BASE_SHA" != "$METRICS_BASE_SHA" ]; then
                degrade_gate snapshot_mismatch
            fi
        fi

        # Verify fixed path names, byte lengths, and SHA-256 digests.
        for artifact_key in annotated_diff hunk_ranges valid_lines; do
            case "$artifact_key" in
              annotated_diff)
                artifact_path="${annotated_diff_path:-}"
                retained_path="${ARTIFACT_SNAPSHOT_DIR}/annotated_diff"
                ANNOTATED_DIFF_SNAPSHOT_PATH="$retained_path"
                ;;
              hunk_ranges)
                artifact_path="${hunk_ranges_path:-}"
                retained_path="${ARTIFACT_SNAPSHOT_DIR}/hunk_ranges"
                HUNK_RANGES_SNAPSHOT_PATH="$retained_path"
                ;;
              valid_lines)
                artifact_path="${valid_lines_path:-}"
                retained_path="${ARTIFACT_SNAPSHOT_DIR}/valid_lines"
                VALID_LINES_SNAPSHOT_PATH="$retained_path"
                ;;
            esac
            expected_name="$(jq -r ".artifacts.${artifact_key}.basename // \"\"" < "$METRICS_MARKER_BEFORE")"
            expected_length="$(jq -r ".artifacts.${artifact_key}.byte_length // \"\"" < "$METRICS_MARKER_BEFORE")"
            expected_digest="$(jq -r ".artifacts.${artifact_key}.sha256 // \"\"" < "$METRICS_MARKER_BEFORE")"
            if [ -z "$artifact_path" ] || [ ! -f "$artifact_path" ]; then
                degrade_gate artifact_missing
            elif [ "$(basename "$artifact_path")" != "$expected_name" ]; then
                degrade_gate artifact_name_mismatch
            elif ! cp -- "$artifact_path" "$retained_path"; then
                degrade_gate artifact_missing
            elif [ "$(wc -c < "$retained_path" | tr -d ' ')" != "$expected_length" ]; then
                degrade_gate artifact_length_mismatch
            elif [ "$(sha256sum "$retained_path" | cut -d' ' -f1)" != "$expected_digest" ]; then
                degrade_gate artifact_digest_mismatch
            fi
        done

        if ! cp -- "$diff_metrics_path" "$METRICS_MARKER_AFTER" ||
           ! cmp -s "$METRICS_MARKER_BEFORE" "$METRICS_MARKER_AFTER"; then
            degrade_gate marker_changed
        elif ! jq -e 'has("run_overengineering_audits")' < "$METRICS_MARKER_BEFORE" >/dev/null; then
            degrade_gate gate_missing
        elif ! jq -e '.run_overengineering_audits | type == "boolean"' < "$METRICS_MARKER_BEFORE" >/dev/null; then
            degrade_gate gate_not_boolean
        elif [ "$GATE_FAILED" = true ]; then
            : # Retain the first deterministic validation failure.
        elif [ "$(jq -r '.run_overengineering_audits' < "$METRICS_MARKER_BEFORE")" = true ]; then
            GATE_STATE=valid_true
            GATE_REASON_CODE=none
            EXPERIMENTAL_AUDIT_STATE=pending
        else
            GATE_STATE=valid_false
            GATE_REASON_CODE=none
            EXPERIMENTAL_AUDIT_STATE=not_required
        fi

        if [ "$GATE_STATE" = valid_true ] || [ "$GATE_STATE" = valid_false ]; then
            # Consume only the retained, digest-validated sidecars. Keep these files
            # for final pre-effect revalidation; never reread the publisher paths.
            ANNOTATED_DIFF="$(tail -n +2 "$ANNOTATED_DIFF_SNAPSHOT_PATH")"
            VALID_LINE_RANGES="$(cat "$HUNK_RANGES_SNAPSHOT_PATH")"
            VALID_DIFF_LINES="$(cat "$VALID_LINES_SNAPSHOT_PATH")"
        fi
    fi
fi

    GATE_AUTHORITY="$(jq -cn \
      --arg state "$GATE_STATE" \
      --arg reason_code "$GATE_REASON_CODE" \
      --arg experimental_audit_state "$EXPERIMENTAL_AUDIT_STATE" \
      --arg head_sha "$METRICS_HEAD_SHA" \
      --arg base_sha "$METRICS_BASE_SHA" \
      --arg merge_base_sha "$METRICS_MERGE_BASE_SHA" \
      --arg base_repo_full_name "$METRICS_BASE_REPO_FULL_NAME" \
      --arg diff_sha256 "$DIFF_SHA256" \
      --arg profile_id "$PROFILE_ID" \
      --arg annotation_generation_id "$ANNOTATION_GENERATION_ID" \
      --arg authority_path "$authority_path" \
      --arg snapshot_dir "$snapshot_dir" \
      --arg metrics_marker_snapshot_path "$METRICS_MARKER_BEFORE" \
      --arg annotated_diff_snapshot_path "$ANNOTATED_DIFF_SNAPSHOT_PATH" \
      --arg hunk_ranges_snapshot_path "$HUNK_RANGES_SNAPSHOT_PATH" \
      --arg valid_lines_snapshot_path "$VALID_LINES_SNAPSHOT_PATH" \
      --arg mode "$mode" \
      --arg checkout_root "$checkout_root" \
      --arg pr_number "$pr_number" \
      --arg diff_metrics_path "$diff_metrics_path" \
      --arg annotated_diff_path "$annotated_diff_path" \
      --arg hunk_ranges_path "$hunk_ranges_path" \
      --arg valid_lines_path "$valid_lines_path" \
      '{state:$state,reason_code:$reason_code,
        experimental_audit_state:$experimental_audit_state,
        snapshot:{head_sha:$head_sha,base_sha:$base_sha,merge_base_sha:$merge_base_sha,
                  base_repo_full_name:$base_repo_full_name,
                  diff_sha256:$diff_sha256,profile_id:$profile_id},
        annotation_generation_id:$annotation_generation_id,
        authority_path:$authority_path,snapshot_dir:$snapshot_dir,
        metrics_marker_snapshot_path:$metrics_marker_snapshot_path,
        annotated_diff_snapshot_path:$annotated_diff_snapshot_path,
        hunk_ranges_snapshot_path:$hunk_ranges_snapshot_path,
        valid_lines_snapshot_path:$valid_lines_snapshot_path,
        mode:$mode,checkout_root:$checkout_root,pr_number:$pr_number,
        diff_metrics_path:$diff_metrics_path,annotated_diff_path:$annotated_diff_path,
        hunk_ranges_path:$hunk_ranges_path,valid_lines_path:$valid_lines_path}')" || return 2
    authority_tmp="$(mktemp "$snapshot_dir/gate_authority.json.tmp.XXXXXX")" || return 2
    printf '%s\n' "$GATE_AUTHORITY" > "$authority_tmp" || return 2
    mv -- "$authority_tmp" "$authority_path" || return 2
    printf '%s\n' "$GATE_AUTHORITY"
}

revalidate() {
    local authority_path="$1" authority state checkout_root mode pr_number
    local metrics_marker_snapshot_path annotated_diff_snapshot_path
    local hunk_ranges_snapshot_path valid_lines_snapshot_path diff_metrics_path
    local annotated_diff_path hunk_ranges_path valid_lines_path
    local head_sha base_sha merge_base_sha base_repo_full_name
    local current_head current_merge_base current_live_refs current_live_head
    local current_live_base current_live_repo current_live_merge_base
    if [[ ! -r "$authority_path" ]] ||
       ! jq -e '
         type == "object" and
         (.state | type == "string") and
         (.reason_code | type == "string") and
         (.experimental_audit_state | type == "string") and
         (.snapshot | type == "object" and
           (.head_sha | type == "string") and
           (.base_sha | type == "string") and
           (.merge_base_sha | type == "string") and
           (.base_repo_full_name | type == "string") and
           (.diff_sha256 | type == "string") and
           (.profile_id | type == "string")) and
         (.annotation_generation_id | type == "string") and
         (.authority_path | type == "string") and
         (.snapshot_dir | type == "string") and
         (.metrics_marker_snapshot_path | type == "string") and
         (.annotated_diff_snapshot_path | type == "string") and
         (.hunk_ranges_snapshot_path | type == "string") and
         (.valid_lines_snapshot_path | type == "string") and
         (.mode | type == "string") and
         (.checkout_root | type == "string") and
         (.pr_number | type == "string") and
         (.diff_metrics_path | type == "string") and
         (.annotated_diff_path | type == "string") and
         (.hunk_ranges_path | type == "string") and
         (.valid_lines_path | type == "string")
       ' "$authority_path" >/dev/null 2>&1; then
        printf 'invalid gate authority\n' >&2
        return 2
    fi
    authority="$(cat "$authority_path")"
    state="$(jq -r '.state' <<< "$authority")"
    if [[ "$state" != valid_true && "$state" != valid_false ]]; then
        printf 'authority_degraded\n'
        return 0
    fi
    checkout_root="$(jq -r '.checkout_root' <<< "$authority")"
    mode="$(jq -r '.mode' <<< "$authority")"
    pr_number="$(jq -r '.pr_number' <<< "$authority")"
    diff_metrics_path="$(jq -r '.diff_metrics_path' <<< "$authority")"
    annotated_diff_path="$(jq -r '.annotated_diff_path' <<< "$authority")"
    hunk_ranges_path="$(jq -r '.hunk_ranges_path' <<< "$authority")"
    valid_lines_path="$(jq -r '.valid_lines_path' <<< "$authority")"
    metrics_marker_snapshot_path="$(jq -r '.metrics_marker_snapshot_path' <<< "$authority")"
    annotated_diff_snapshot_path="$(jq -r '.annotated_diff_snapshot_path' <<< "$authority")"
    hunk_ranges_snapshot_path="$(jq -r '.hunk_ranges_snapshot_path' <<< "$authority")"
    valid_lines_snapshot_path="$(jq -r '.valid_lines_snapshot_path' <<< "$authority")"
    head_sha="$(jq -r '.snapshot.head_sha' <<< "$authority")"
    base_sha="$(jq -r '.snapshot.base_sha' <<< "$authority")"
    merge_base_sha="$(jq -r '.snapshot.merge_base_sha' <<< "$authority")"
    base_repo_full_name="$(jq -r '.snapshot.base_repo_full_name' <<< "$authority")"
    if [[ "$mode" != local && "$mode" != github ]] ||
       [[ -z "$checkout_root" || -z "$pr_number" || -z "$head_sha" || -z "$base_sha" ]] ||
       [[ -z "$metrics_marker_snapshot_path" || -z "$annotated_diff_snapshot_path" ]] ||
       [[ -z "$hunk_ranges_snapshot_path" || -z "$valid_lines_snapshot_path" ]]; then
        printf 'invalid gate authority\n' >&2
        return 2
    fi
    if ! cd "$checkout_root" 2>/dev/null ||
       ! cmp -s -- "$diff_metrics_path" "$metrics_marker_snapshot_path" ||
       ! cmp -s -- "$annotated_diff_path" "$annotated_diff_snapshot_path" ||
       ! cmp -s -- "$hunk_ranges_path" "$hunk_ranges_snapshot_path" ||
       ! cmp -s -- "$valid_lines_path" "$valid_lines_snapshot_path"; then
        printf 'stale\n'
        return 0
    fi
    current_head="$(git rev-parse HEAD 2>/dev/null || true)"
    if [[ "$current_head" != "$head_sha" ]]; then
        printf 'stale\n'
        return 0
    fi
    if [[ "$mode" == local ]]; then
        current_live_refs="$(gh api "repos/{owner}/{repo}/pulls/${pr_number}" \
            --jq '{headRefOid:.head.sha,baseRefOid:.base.sha,baseRepoFullName:.base.repo.full_name}' 2>/dev/null || true)"
        current_live_head="$(jq -r '.headRefOid // ""' <<< "$current_live_refs" 2>/dev/null)"
        current_live_base="$(jq -r '.baseRefOid // ""' <<< "$current_live_refs" 2>/dev/null)"
        current_live_repo="$(jq -r '.baseRepoFullName // ""' <<< "$current_live_refs" 2>/dev/null)"
        current_live_merge_base="$(gh api \
            "repos/${current_live_repo}/compare/${current_live_base}...${current_live_head}" \
            --jq '.merge_base_commit.sha' 2>/dev/null || true)"
        current_merge_base="$(git merge-base "$base_sha" "$current_head" 2>/dev/null || true)"
        if [[ "$current_live_head" != "$head_sha" ||
              "$current_live_base" != "$base_sha" ||
              "$current_live_repo" != "$base_repo_full_name" ||
              "$current_live_merge_base" != "$merge_base_sha" ||
              "$current_merge_base" != "$merge_base_sha" ]]; then
            printf 'stale\n'
            return 0
        fi
    else
        current_live_refs="$(gh api "repos/{owner}/{repo}/pulls/${pr_number}" \
            --jq '{headRefOid:.head.sha,baseRefOid:.base.sha}' 2>/dev/null || true)"
        current_live_head="$(jq -r '.headRefOid // ""' <<< "$current_live_refs" 2>/dev/null)"
        current_live_base="$(jq -r '.baseRefOid // ""' <<< "$current_live_refs" 2>/dev/null)"
        if [[ "$current_live_head" != "$head_sha" || "$current_live_base" != "$base_sha" ]]; then
            printf 'stale\n'
            return 0
        fi
    fi
    printf 'fresh\n'
}

case "${1:-}" in
  snapshot)
    if [[ "$#" -ne 9 ]]; then
        printf 'snapshot requires eight arguments\n' >&2
        exit 2
    fi
    snapshot "${@:2}"
    ;;
  revalidate)
    if [[ "$#" -ne 2 ]]; then
        printf 'revalidate requires one argument\n' >&2
        exit 2
    fi
    revalidate "$2"
    ;;
  *)
    printf 'unknown subcommand\n' >&2
    exit 2
    ;;
esac
