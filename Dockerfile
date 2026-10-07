# Fleetwatch for Epiphan Edge, as a container. Read-only watcher; listens on no port.
#   docker compose run --rm fleetwatch login    # one-time sign-in; paste the redirect URL when asked
#   docker compose up -d                        # heartbeat every 3 minutes
FROM ghcr.io/astral-sh/uv:0.12.16 AS uv

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dependencies first, so code changes don't rebuild them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
LABEL org.opencontainers.image.title="Fleetwatch for Epiphan Edge" \
      org.opencontainers.image.description="Always-on, read-only watcher for an Epiphan Edge fleet" \
      org.opencontainers.image.source="https://github.com/ScientiaCapital/fleetwatch" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN useradd --create-home --uid 10001 fleetwatch \
 && mkdir -p /home/fleetwatch/.fleetwatch && chown fleetwatch:fleetwatch /home/fleetwatch/.fleetwatch
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY policy.yaml tool_policy.yaml ./
COPY tests/fixtures ./tests/fixtures
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER fleetwatch
# Token and state live here. Mount a volume so they survive restarts.
VOLUME ["/home/fleetwatch/.fleetwatch"]
ENTRYPOINT ["fleetwatch"]
CMD ["run"]
