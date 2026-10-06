# Sourced by CI and the local smoke wrapper with errexit disabled.
# Sets rm_status, verify_status, removal_status and cleanup_status for lifecycle reporting.
cleanup_smoke_resources() {
  local cid_file=$1 scripts_dir=$2 out_dir=$3 image=$4 test=$5
  local container_id remaining
  rm_status=1
  verify_status=1
  removal_status=0

  if [[ ! -s "$cid_file" ]]; then
    echo "missing container ID evidence: $cid_file" >&2
    removal_status=1
  else
    container_id="$(<"$cid_file")"
    if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
      echo "invalid container ID evidence retained: $cid_file" >&2
      removal_status=1
    else
      timeout --signal=TERM --kill-after=10s 60s docker rm -f "$container_id"
      rm_status=$?
      remaining="$(timeout --signal=TERM --kill-after=10s 60s \
        docker container ls --all --quiet --no-trunc --filter "id=$container_id")"
      verify_status=$?
      if (( verify_status != 0 )) || [[ -n "$remaining" ]]; then
        echo "container removal could not be verified; retaining $cid_file" >&2
        removal_status=1
      elif ! rm -f -- "$cid_file"; then
        echo "could not remove verified container ID file: $cid_file" >&2
        removal_status=1
      elif (( rm_status != 0 )); then
        echo "container was already absent; exact-ID absence verified"
      fi
    fi
  fi

  timeout --signal=TERM --kill-after=20s 300s docker run --rm \
    --env E2E_SANDBOX_TOKEN \
    --volume "$scripts_dir:/opt/e2e:ro" \
    --volume "$out_dir:/artifacts" \
    "$image" python3 /opt/e2e/e2e_harness.py cleanup \
      --test "$test" --catalog /opt/e2e/catalog.json --out /artifacts
  cleanup_status=$?
}

finish_smoke_lifecycle() {
  local lifecycle_file=$1 model_status=$2 redact_status=${3:-0}
  {
    printf 'model_exit=%s\ndocker_rm=%s\ncontainer_verify=%s\ncontainer_removal=%s\nremote_cleanup=%s\n' \
      "$model_status" "$rm_status" "$verify_status" "$removal_status" "$cleanup_status"
    if (( $# == 3 )); then
      printf 'redaction=%s\n' "$redact_status"
    fi
  } > "$lifecycle_file" || return 1
  (( model_status == 0 && removal_status == 0 && cleanup_status == 0 && redact_status == 0 ))
}
