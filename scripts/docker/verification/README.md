# Verification image

Build from a clean committed worktree. The archive supplies source files; a Git
bundle supplies the same commit's index and reachable history for tests that
inspect tracked files or historical baselines. Host Git configuration, hooks, and
unrelated refs are not copied.

```bash
source_sha=$(git rev-parse HEAD)
build_root="$PWD/.autoskillit/temp/verification/$source_sha"
mkdir -p "$build_root/history"
git bundle create "$build_root/history/source.bundle" HEAD
git archive "$source_sha" | docker build \
  --build-context "source_history=$build_root/history" \
  --build-arg "SOURCE_SHA=$source_sha" \
  --file scripts/docker/verification/Dockerfile \
  --tag "autoskillit-verification:$source_sha" -
```

If the locked Git dependency requires authentication, add
`--secret id=github_token,env=AUTOSKILLIT_VERIFICATION_GITHUB_TOKEN` with that
environment variable set in the calling process. It is used only during the
dependency-install build step.

The process-lifecycle tests require an init process to reap orphan children and an
executable shared-memory filesystem. The sandbox tests also need their nested
sandbox operations allowed by the container security profile:

```bash
docker run --rm --init \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  --tmpfs /dev/shm:rw,exec,nosuid,nodev,size=4g,mode=1777 \
  "autoskillit-verification:$source_sha" task test-local-gate
```

The separate `task test-smoke-native-join-live-gates` requires authenticated Claude
and Codex clients. Copy read-only credential mounts into isolated container homes
with mode 0600; mount `/workspace/.autoskillit/temp/native-join-live` onto a local
evidence directory so raw native output survives container removal. Preserve the
source SHA, image ID, command, exit status, and logs for each verification run.
