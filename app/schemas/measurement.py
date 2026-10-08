import uuid

from pydantic import BaseModel, Field

from app.core.enums import FileStatus, MeasurementStatus, MeasurementType
from app.repositories.measurement_repository import FeatureMeasurement
from app.services.file_service import MeasurementPage


class MeasurementResponse(BaseModel):
    feature_id: uuid.UUID
    feature_index: int
    geometry_type: str | None = Field(examples=["Polygon"])
    measurement_type: MeasurementType
    value: float | None = Field(description="Set only when status is CALCULATED.")
    unit: str | None = Field(examples=["m²"])
    measurement_crs: str | None = Field(
        description="Projected CRS the value was computed in.", examples=["EPSG:32643"]
    )
    status: MeasurementStatus
    error_message: str | None

    @classmethod
    def from_row(cls, row: FeatureMeasurement) -> "MeasurementResponse":
        measurement = row.measurement
        return cls(
            feature_id=measurement.feature_id,
            feature_index=row.feature_index,
            geometry_type=row.geometry_type,
            measurement_type=measurement.measurement_type,
            value=measurement.value,
            unit=measurement.unit,
            measurement_crs=measurement.measurement_crs,
            status=measurement.status,
            error_message=measurement.error_message,
        )


class MeasurementSummary(BaseModel):
    """Measurement counts by status, over the whole file (not just this page)."""

    calculated: int
    not_applicable: int
    unsupported: int
    failed: int

    @classmethod
    def from_counts(cls, counts: dict[MeasurementStatus, int]) -> "MeasurementSummary":
        return cls(
            calculated=counts[MeasurementStatus.CALCULATED],
            not_applicable=counts[MeasurementStatus.NOT_APPLICABLE],
            unsupported=counts[MeasurementStatus.UNSUPPORTED],
            failed=counts[MeasurementStatus.FAILED],
        )


class MeasurementListResponse(BaseModel):
    file_id: uuid.UUID
    file_status: FileStatus
    total: int
    limit: int
    offset: int
    summary: MeasurementSummary
    items: list[MeasurementResponse]

    @classmethod
    def from_page(cls, page: MeasurementPage, limit: int, offset: int) -> "MeasurementListResponse":
        return cls(
            file_id=page.file.id,
            file_status=page.file.status,
            total=page.total,
            limit=limit,
            offset=offset,
            summary=MeasurementSummary.from_counts(page.summary),
            items=[MeasurementResponse.from_row(row) for row in page.items],
        )
