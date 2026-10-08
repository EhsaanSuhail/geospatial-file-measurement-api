import logging

from fastapi import APIRouter, Response, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import SessionDep
from app.db.session import check_database_connection
from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service and database health",
    responses={503: {"model": HealthResponse, "description": "The database is unreachable."}},
)
def health(session: SessionDep, response: Response) -> HealthResponse:
    try:
        check_database_connection(session)
    except SQLAlchemyError:
        logger.warning("Health check failed: database unreachable", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="unavailable", database="unavailable")
    return HealthResponse(status="ok", database="ok")
