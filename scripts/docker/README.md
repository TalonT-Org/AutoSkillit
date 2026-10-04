# Docker images

## Targets and pins

`scripts/docker/Dockerfile` builds three stages from one toolchain:

| Target | Purpose |
|--------|---------|
| `toolchain` | Every pinned tool: Claude Code, Codex, uv, Node, Rust, Task, pre-commit, ripgrep, jq, gh |
| `dev` | The verification image: a locked development checkout that runs the test gates |
| `user` | An installed copy of AutoSkillit, as a user gets it; the default target |

The `ARG` pins in `scripts/docker/Dockerfile` are the only place to change a tool
version. `tests/infra/test_docker_image.py` fails until every CI step,
Taskfile precondition and code constant that installs or requires that tool agrees.
Node, pre-commit, jq and gh are pinned only there: CI uses the runner's Node to host
npm and does not install the others. Changing `CODEX_VERSION` also requires rerunning
the [Codex shell baseline](#codex-shell-baseline).

## Dev image

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
  --file scripts/docker/Dockerfile \
  --target dev \
  --tag "autoskillit-dev:$source_sha" -
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
  "autoskillit-dev:$source_sha" task test-local-gate
```

The separate `task test-smoke-native-join-live-gates` requires authenticated Claude
and Codex clients. Copy read-only credential mounts into isolated container homes
with mode 0600; mount `/workspace/.autoskillit/temp/native-join-live` onto a local
evidence directory so raw native output survives container removal. Preserve the
source SHA, image ID, command, exit status, and logs for each verification run.

## User image

Build any committed SHA, pushed or not, with no token:

```bash
source_sha=$(git rev-parse HEAD)
git archive "$source_sha" | docker build --file scripts/docker/Dockerfile \
  --target user --tag "autoskillit-user:$source_sha" -
scripts/docker/verify-image "autoskillit-user:$source_sha"
```

The image installs AutoSkillit with `uv tool install` into uv's default layout under
`/home/autoskillit` (`~/.local/share/uv/tools/autoskillit`, `~/.local/bin/autoskillit`),
exactly where a user's own install lives. `autoskillit doctor`, the
installation-integrity guard and the entrypoint shim all resolve those
HOME-relative paths, so a relocated tool or bin directory would read as a broken
install. The build mounts its context for the install step only: no source tree, Git
metadata or token reaches the image.

Credentials are never baked in; supply them at run time.

## Published images

`.github/workflows/docker-image.yml` builds the `user` target and publishes it to
Docker Hub as `trecek/autoskillit`:

| Tag | Moves on |
|-----|----------|
| `<version>`, `sha-<short>` | Every publication |
| `develop` | Each develop push that changes the version |
| `stable`, `latest` | Each `v*` release tag |

Pull without logging in. After pushing, the publish job logs out and runs
`scripts/docker/verify-image` against the pushed image, which checks the AutoSkillit,
Claude Code and Codex versions against `pyproject.toml` and the Dockerfile pins and
fails when the image history holds a credential.

## Session container

`scripts/docker/autoskillit-container` keeps a persistent session on a published image:

| Command | Effect |
|---------|--------|
| `update` | Pull the published image and recreate the session container |
| `start` | Start the session container, creating it with `update` if necessary |
| `stop` | Stop the session container without deleting its volumes |
| `shell` | Start the session and open a shell in `/workspace/AutoSkillit` |
| `status` | Show the image, container, tool versions and authentication status |
| `sync-auth` | Copy host credentials into the home volume |

| Variable | Default |
|----------|---------|
| `AUTOSKILLIT_IMAGE_TAG` | `develop` |
| `AUTOSKILLIT_CONTAINER_NAME` | `autoskillit-session` |
| `AUTOSKILLIT_CPUS` | `2` |
| `AUTOSKILLIT_MEMORY` | `4g` (also the memory-plus-swap limit) |

`update` pulls `trecek/autoskillit:${AUTOSKILLIT_IMAGE_TAG:-develop}` and keeps the
`<name>-home` and `<name>-workspace` volumes. Docker seeds only an empty volume from
an image, so `update` replaces AutoSkillit's uv tool installation in an existing home
with the pulled image's copy and keeps everything else there (agent logins, settings,
logs). Run `autoskillit install` in the session after an update, as after any install.
`update` refuses while AutoSkillit, Claude or Codex processes are active, and leaves
the session stopped unless it was running.

`sync-auth` copies host Codex, Claude Code, GitHub CLI and AutoSkillit provider
credentials into the home volume with mode 0600; treat that volume as
credential-bearing. Nothing is built locally and no credential enters an image.

Attach editors to the named session container rather than running the image
directly: a bare `docker run` has neither volume.

## Codex shell baseline

Run the opt-in live host comparison inside the dev image:

```bash
python scripts/docker/codex_shell_baseline.py
```

The tool runs the matrix's native Codex cases once with no hooks and once with
only the generated shell-capture hook. It requires Codex authentication in the
container. Host-lifetime cases end Codex after the command leader starts. The tool
creates throwaway Codex homes and keeps bounded JSONL and stderr logs
under `.autoskillit/temp/codex_shell_baseline/`. The `CODEX_VERSION` pin is part
of the expected baseline: rerun this comparison whenever that Dockerfile value
changes, then update the matrix only after reviewing the recorded results.
