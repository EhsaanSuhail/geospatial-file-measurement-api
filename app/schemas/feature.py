import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import FileStatus
from app.models import Feature
from app.services.file_service import FeaturePage


class FeatureResponse(BaseModel):
    id: uuid.UUID
    feature_index: int = Field(description="Position of the feature in the file, from 0.")
    layer: str = Field(description="KML folder or Shapefile name the feature belongs to.")
    geometry_type: str | None = Field(examples=["Polygon"])
    crs: str | None = Field(description="CRS of `geometry`: the file's source CRS.")
    geometry: dict[str, Any] | None = Field(
        description="GeoJSON geometry object, with coordinates in the source CRS."
    )
    geometry_error: str | None = Field(
        description="Why `geometry` is null although the source feature had one."
    )
    properties: dict[str, Any]

    @classmethod
    def from_record(cls, feature: Feature, crs: str | None) -> "FeatureResponse":
        return cls(
            id=feature.id,
            feature_index=feature.feature_index,
            layer=feature.layer_name,
            geometry_type=feature.geometry_type,
            crs=crs,
            geometry=feature.geometry,
            geometry_error=feature.geometry_error,
            properties=feature.properties,
        )


class FeatureListResponse(BaseModel):
    file_id: uuid.UUID
    file_status: FileStatus
    total: int
    limit: int
    offset: int
    items: list[FeatureResponse]

    @classmethod
    def from_page(cls, page: FeaturePage, limit: int, offset: int) -> "FeatureListResponse":
        return cls(
            file_id=page.file.id,
            file_status=page.file.status,
            total=page.total,
            limit=limit,
            offset=offset,
            items=[
                FeatureResponse.from_record(item, page.file.original_crs) for item in page.items
            ],
        )
