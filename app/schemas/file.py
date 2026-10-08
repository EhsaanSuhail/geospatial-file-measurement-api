import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import FileFormat, FileStatus
from app.models import FileRecord


class FileResponse(BaseModel):
    id: uuid.UUID
    filename: str = Field(examples=["survey.kml"])
    format: FileFormat
    file_size_bytes: int
    crs: str | None = Field(
        description=(
            "Source CRS of the file: an authority code (e.g. EPSG:4326) when identifiable, "
            "otherwise its WKT; null when the file defines none."
        ),
        examples=["EPSG:4326"],
    )
    feature_count: int = Field(examples=[120])
    status: FileStatus
    error_message: str | None = Field(description="Why processing failed, when status is FAILED.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: FileRecord) -> "FileResponse":
        return cls(
            id=record.id,
            filename=record.filename,
            format=record.file_format,
            file_size_bytes=record.file_size_bytes,
            crs=record.original_crs,
            feature_count=record.feature_count,
            status=record.status,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
