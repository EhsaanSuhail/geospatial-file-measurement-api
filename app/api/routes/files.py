"""HTTP layer for files: request parsing and response mapping only, no GIS logic."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Request, Response, UploadFile, status

from app.api.dependencies import FileServiceDep, PaginationDep
from app.schemas.error import ErrorResponse
from app.schemas.feature import FeatureListResponse
from app.schemas.file import FileResponse
from app.schemas.measurement import MeasurementListResponse

router = APIRouter(prefix="/files", tags=["files"])

_ERROR_DESCRIPTIONS = {
    404: "The file does not exist.",
    413: "The upload, its uncompressed contents or its feature count exceed a limit.",
    415: "The file is neither a KML file nor a ZIP archive.",
    422: "Invalid path or query parameters (INVALID_REQUEST).",
    500: "Processing failed unexpectedly (PROCESSING_FAILED).",
    503: "The database is unavailable (DATABASE_ERROR).",
}
_UPLOAD_UNPROCESSABLE = (
    "The file content cannot be processed (MALFORMED_FILE, INVALID_SHAPEFILE_ARCHIVE), or the "
    "request is invalid, e.g. no `file` part (INVALID_REQUEST)."
)


def _errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]}
        for code in status_codes
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process a KML file or a zipped Shapefile",
    responses={
        **_errors(413, 415, 500, 503),
        422: {"model": ErrorResponse, "description": _UPLOAD_UNPROCESSABLE},
    },
)
def upload_file(
    file: Annotated[
        UploadFile, File(description="A .kml file, or a .zip archive containing one Shapefile.")
    ],
    service: FileServiceDep,
    request: Request,
    response: Response,
) -> FileResponse:
    record = service.upload(file.file, file.filename)
    response.headers["Location"] = str(request.url_for("get_file", file_id=record.id))
    return FileResponse.from_record(record)


@router.get(
    "/{file_id}",
    name="get_file",
    summary="File metadata and processing status",
    responses=_errors(404, 422, 503),
)
def get_file(file_id: uuid.UUID, service: FileServiceDep) -> FileResponse:
    return FileResponse.from_record(service.get_file(file_id))


@router.get(
    "/{file_id}/features",
    summary="Extracted features: index, geometry type, GeoJSON geometry, CRS and attributes",
    responses=_errors(404, 422, 503),
)
def list_features(
    file_id: uuid.UUID, service: FileServiceDep, page: PaginationDep
) -> FeatureListResponse:
    result = service.list_features(file_id, page.limit, page.offset)
    return FeatureListResponse.from_page(result, page.limit, page.offset)


@router.get(
    "/{file_id}/measurements",
    summary="Feature-level measurements: polygon area (m²) and line length (m)",
    responses=_errors(404, 422, 503),
)
def list_measurements(
    file_id: uuid.UUID, service: FileServiceDep, page: PaginationDep
) -> MeasurementListResponse:
    result = service.list_measurements(file_id, page.limit, page.offset)
    return MeasurementListResponse.from_page(result, page.limit, page.offset)
