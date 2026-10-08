from typing import Any


class AppError(Exception):
    """Base class for errors that are safe to report to API clients.

    `message` is client-facing and must never contain stack traces, filesystem paths,
    SQL or GDAL internals. Diagnostic detail belongs in server logs only.
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class FileRecordNotFoundError(AppError):
    status_code = 404
    code = "FILE_NOT_FOUND"


class FileTooLargeError(AppError):
    status_code = 413
    code = "FILE_TOO_LARGE"


class UnsupportedFileFormatError(AppError):
    status_code = 415
    code = "UNSUPPORTED_FILE_FORMAT"


class MalformedFileError(AppError):
    """The upload claims a supported format but its content cannot be read as such."""

    status_code = 422
    code = "MALFORMED_FILE"


class InvalidShapefileArchiveError(AppError):
    """The ZIP archive is readable but does not contain exactly one complete Shapefile."""

    status_code = 422
    code = "INVALID_SHAPEFILE_ARCHIVE"


class ProcessingError(AppError):
    status_code = 500
    code = "PROCESSING_FAILED"


class DatabaseError(AppError):
    status_code = 503
    code = "DATABASE_ERROR"
