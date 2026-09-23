# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.9.21 AS uv
FROM rust:1.90-bookworm

ARG CODEX_VERSION=0.147.0
ARG CLAUDE_VERSION=2.1.219
ARG GO_TASK_VERSION=3.43.3
ARG PRE_COMMIT_VERSION=4.6.2
ARG VERIFIER_UID=1000
ARG VERIFIER_GID=1000

COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
        python-is-python3 \
        python3 \
        python3-dev \
        python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --omit=dev \
        "@openai/codex@${CODEX_VERSION}" \
        "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
    && rm -rf /root/.npm \
    && task_arch="$(dpkg --print-architecture)" \
    && task_release="https://github.com/go-task/task/releases/download/v${GO_TASK_VERSION}" \
    && curl --fail --location --silent --show-error \
        "${task_release}/task_linux_${task_arch}.tar.gz" \
        | tar --extract --gzip --directory /usr/local/bin task \
    && uv pip install --system "pre-commit==${PRE_COMMIT_VERSION}" \
    && test "$(codex --version 2>/dev/null)" = "codex-cli ${CODEX_VERSION}" \
    && claude --version 2>/dev/null | grep --quiet "^${CLAUDE_VERSION}"

RUN groupadd --gid "${VERIFIER_GID}" verifier \
    && useradd --uid "${VERIFIER_UID}" --gid verifier --create-home --shell /bin/bash verifier \
    && install --directory --owner verifier --group verifier /opt/venv /workspace

ENV HOME=/home/verifier \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /workspace
COPY --chown=verifier:verifier . /workspace

USER verifier

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

CMD ["bash"]
