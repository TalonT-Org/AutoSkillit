# syntax=docker/dockerfile:1.7

# Toolchain pins: each tool version in these images is declared once, here or at the top
# of the toolchain stage. tests/infra/test_docker_image.py requires every CI
# step, Taskfile precondition and code constant that installs or requires one to match.
ARG UV_VERSION=0.9.21
ARG NODE_VERSION=24.21.0
ARG RUST_VERSION=1.98.1

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM node:${NODE_VERSION}-bookworm AS node
FROM rust:${RUST_VERSION}-bookworm AS toolchain

ARG CODEX_VERSION=0.156.1
ARG CLAUDE_VERSION=2.1.280
ARG GO_TASK_VERSION=3.43.3
ARG PRE_COMMIT_VERSION=4.6.2
ARG RIPGREP_VERSION=15.2.0
ARG JQ_VERSION=1.8.2
ARG GH_VERSION=2.102.0

COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        git \
        python-is-python3 \
        python3 \
        python3-dev \
        python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global --omit=dev \
        "@openai/codex@${CODEX_VERSION}" \
        "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
    && rm -rf /root/.npm \
    && task_arch="$(dpkg --print-architecture)" \
    && task_release="https://github.com/go-task/task/releases/download/v${GO_TASK_VERSION}" \
    && curl --fail --location --silent --show-error \
        "${task_release}/task_linux_${task_arch}.tar.gz" \
        | tar --extract --gzip --directory /usr/local/bin task \
    && uv venv /opt/pre-commit \
    && uv pip install --python /opt/pre-commit/bin/python \
        "pre-commit==${PRE_COMMIT_VERSION}" \
    && ln -s /opt/pre-commit/bin/pre-commit /usr/local/bin/pre-commit \
    && test "$(codex --version 2>/dev/null)" = "codex-cli ${CODEX_VERSION}" \
    && claude --version 2>/dev/null | grep --quiet "^${CLAUDE_VERSION}"

RUN tool_arch="$(dpkg --print-architecture)" \
    && case "${tool_arch}" in \
        amd64) rg_arch=x86_64 ;; \
        arm64) rg_arch=aarch64 ;; \
        *) exit 1 ;; \
    esac \
    && rg_archive="ripgrep-${RIPGREP_VERSION}-${rg_arch}-unknown-linux-musl" \
    && curl --fail --location --silent --show-error \
        "https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/${rg_archive}.tar.gz" \
        | tar --extract --gzip --strip-components=1 --directory /usr/local/bin "${rg_archive}/rg" \
    && curl --fail --location --silent --show-error --output /usr/local/bin/jq \
        "https://github.com/jqlang/jq/releases/download/jq-${JQ_VERSION}/jq-linux-${tool_arch}" \
    && chmod 0755 /usr/local/bin/jq \
    && gh_archive="gh_${GH_VERSION}_linux_${tool_arch}" \
    && curl --fail --location --silent --show-error \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${gh_archive}.tar.gz" \
        | tar --extract --gzip --strip-components=2 --directory /usr/local/bin \
            "${gh_archive}/bin/gh" \
    && rg --version | grep --quiet "^ripgrep ${RIPGREP_VERSION}" \
    && test "$(jq --version)" = "jq-${JQ_VERSION}" \
    && gh --version | grep --quiet "^gh version ${GH_VERSION} "

FROM toolchain AS dev

ARG VERIFIER_UID=1000
ARG VERIFIER_GID=1000

RUN groupadd --gid "${VERIFIER_GID}" verifier \
    && useradd --uid "${VERIFIER_UID}" --gid verifier --create-home --shell /bin/bash verifier \
    && install --directory --owner verifier --group verifier /workspace

ENV HOME=/home/verifier \
    PATH=/workspace/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    UV_PROJECT_ENVIRONMENT=/workspace/.venv

WORKDIR /workspace
COPY --chown=verifier:verifier . /workspace
RUN install --directory --owner verifier --group verifier /workspace/.autoskillit/temp

USER verifier

ARG SOURCE_SHA
RUN --mount=from=source_history,source=source.bundle,target=/source.bundle \
    test -n "${SOURCE_SHA}" \
    && git init \
    && git config user.name "AutoSkillit Verification" \
    && git config user.email "verification@localhost" \
    && git fetch /source.bundle HEAD \
    && git reset --mixed "${SOURCE_SHA}" \
    && test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"

# The optional BuildKit secret is needed only when the locked Git dependency is private.
# Its value is scoped to this RUN instruction and never becomes an image layer or ENV value.
RUN --mount=type=secret,id=github_token,required=false,uid=${VERIFIER_UID} \
    if [ -s /run/secrets/github_token ]; then \
        github_token="$(cat /run/secrets/github_token)"; \
        github_rewrite="url.https://x-access-token:${github_token}@github.com/.insteadOf"; \
        export GIT_CONFIG_COUNT=1; \
        export GIT_CONFIG_KEY_0="${github_rewrite}"; \
        export GIT_CONFIG_VALUE_0="https://github.com/"; \
    fi; \
    uv sync --locked --extra dev

RUN task regen-contracts \
    && task compile-recipes

CMD ["bash"]

FROM toolchain AS user

RUN groupadd --gid 1000 autoskillit \
    && useradd --uid 1000 --gid autoskillit --create-home --shell /bin/bash autoskillit \
    && install --directory --owner autoskillit --group autoskillit /workspace

# uv's default tool layout under HOME, as on a real install: doctor, the
# installation-integrity guard and the entrypoint shim all resolve ~/.local.
ENV HOME=/home/autoskillit \
    PATH=/home/autoskillit/.local/bin:${PATH} \
    AUTOSKILLIT_SKIP_UPDATE_CHECK=1 \
    AUTOSKILLIT_SKIP_STALE_CHECK=1 \
    DISABLE_AUTOUPDATER=1

USER autoskillit
WORKDIR /workspace

# The build context is mounted for this instruction only, so AutoSkillit installs from
# the archived source exactly as a release installs from its Git URL and no source
# tree, token or Git metadata reaches the image.
RUN --mount=type=bind,target=/opt/autoskillit-source \
    uv tool install --no-cache --python /usr/bin/python3 /opt/autoskillit-source \
    && expected_version="$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' /opt/autoskillit-source/pyproject.toml)" \
    && test "$(autoskillit --version)" = "${expected_version}"

CMD ["bash"]
