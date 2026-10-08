"""Translate exceptions into the API's single error envelope: {"error": {"code", "message"}}."""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.core.logging import request_id_var
from app.schemas.error import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

# 400 comes from Starlette when the multipart body itself cannot be parsed.
_HTTP_ERROR_CODES = {
    400: "INVALID_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "FILE_TOO_LARGE",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details or None))
    return JSONResponse(
        status_code=status_code, content=body.model_dump(exclude_none=True), headers=headers
    )


async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    level = logging.ERROR if exc.status_code >= 500 else logging.INFO
    logger.log(level, "Request failed: %s", exc.message, extra={"error_code": exc.code})
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"location": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors()
    ]
    return error_response(422, "INVALID_REQUEST", "The request is invalid.", {"errors": errors})


async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def _handle_database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Database error", exc_info=exc)
    return error_response(
        503, "DATABASE_ERROR", "The database is unavailable or could not complete the request."
    )


async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # Starlette runs this handler in its outermost middleware, outside our request-context
    # middleware, so the request ID header has to be attached here explicitly.
    logger.error("Unhandled error", exc_info=exc)
    request_id = request_id_var.get()
    return error_response(
        500,
        "INTERNAL_SERVER_ERROR",
        "An unexpected error occurred.",
        headers={"X-Request-ID": request_id} if request_id else None,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(SQLAlchemyError, _handle_database_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)
