# syntax=docker/dockerfile:1

# GDAL, GEOS and PROJ ship inside the pyogrio, shapely and pyproj wheels,
# so a slim base image needs no system geospatial packages.
FROM python:3.12-slim AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app


# Resolve dependencies from the lock file. The uv cache lives in a BuildKit cache mount,
# so it never ends up in an image layer.
FROM python-base AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project


# Test image: dev dependencies plus the test suite (`docker compose --profile test run --rm --build tests`).
FROM builder AS test
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra dev
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY tests ./tests
CMD ["pytest"]


# Runtime image (default target): the virtualenv and application code only.
FROM python-base AS runtime
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
RUN useradd --system --uid 10001 geomeasure
USER geomeasure
EXPOSE 8000
# Migrations run before the server starts. Fine for a single instance; with several replicas,
# run `alembic upgrade head` as a separate one-off deployment step instead.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
