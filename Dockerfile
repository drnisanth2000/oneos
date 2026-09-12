# syntax=docker/dockerfile:1.7

FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ARG ONEOS_UID=10001
ARG ONEOS_GID=10001

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN test "${ONEOS_UID}" -gt 0 \
    && test "${ONEOS_GID}" -gt 0 \
    && { getent group "${ONEOS_GID}" >/dev/null || groupadd --gid "${ONEOS_GID}" oneos; } \
    && { getent passwd "${ONEOS_UID}" >/dev/null || useradd --uid "${ONEOS_UID}" --gid "${ONEOS_GID}" --home-dir /tmp --no-create-home --shell /usr/sbin/nologin oneos; }

COPY --from=ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 /uv /usr/local/bin/uv

WORKDIR /app
COPY --chown=${ONEOS_UID}:${ONEOS_GID} pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY --chown=${ONEOS_UID}:${ONEOS_GID} . .
RUN uv sync --locked --no-dev --no-editable \
    && rm -rf /root/.cache/uv

USER ${ONEOS_UID}:${ONEOS_GID}
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS caddy

ARG ONEOS_UID=10001
ARG ONEOS_GID=10001

RUN test "${ONEOS_UID}" -gt 0 \
    && test "${ONEOS_GID}" -gt 0 \
    && chown -R ${ONEOS_UID}:${ONEOS_GID} /data /config

USER ${ONEOS_UID}:${ONEOS_GID}
