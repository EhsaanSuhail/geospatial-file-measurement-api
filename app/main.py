from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    TrailingSlashMiddleware,
)
from app.api.router import api_router
from app.api.routes import health
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, create_session_factory

# Room for multipart boundaries and part headers on top of the file itself.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024

_DESCRIPTION = """
Upload a **KML** file or a **ZIP archive containing a Shapefile**. Every feature is extracted
(index, geometry type, geometry, CRS, attributes) and measured: polygon **area** in m² and
line **length** in m, computed in a projected CRS chosen per feature — never in degrees.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Used by Uvicorn via `--factory`, and by tests with custom settings."""
    settings = settings or Settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="GeoMeasure API",
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = create_session_factory(engine)

    register_exception_handlers(app)
    # Starlette applies middleware in reverse order of registration: the request-context
    # middleware is outermost, so even rejected oversized bodies get a request ID and a log line.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=settings.max_upload_size_bytes + _MULTIPART_OVERHEAD_BYTES,
        message=f"The upload exceeds the limit of {settings.max_upload_size_mb} MB.",
    )
    app.add_middleware(TrailingSlashMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(api_router, prefix="/api/v1")
    # The assignment uses /api/files/...; serve those paths too, but only document /api/v1.
    app.include_router(api_router, prefix="/api", include_in_schema=False)
    return app
