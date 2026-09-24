# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.9.21 AS uv
FROM node:22-bookworm AS node
FROM rust:1.90-bookworm

ARG CODEX_VERSION=0.156.1
ARG CLAUDE_VERSION=2.1.280
ARG GO_TASK_VERSION=3.43.3
ARG PRE_COMMIT_VERSION=4.6.2
ARG MYPY_VERSION=1.19.1
ARG TYPES_PYYAML_VERSION=6.0.12.20250915
ARG RIPGREP_VERSION=15.2.0
ARG JQ_VERSION=1.8.2
ARG VERIFIER_UID=1000
ARG VERIFIER_GID=1000

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
        "mypy==${MYPY_VERSION}" \
        "types-PyYAML==${TYPES_PYYAML_VERSION}" \
    && ln -s /opt/pre-commit/bin/pre-commit /usr/local/bin/pre-commit \
    && ln -s /opt/pre-commit/bin/mypy /usr/local/bin/mypy \
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
    && rg --version | grep --quiet "^ripgrep ${RIPGREP_VERSION}" \
    && test "$(jq --version)" = "jq-${JQ_VERSION}"

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
