"""Measure each feature: polygon area in m² and line length in m, in a projected CRS.

Every feature gets exactly one result, and a problem with one feature never stops the others:
points are NOT_APPLICABLE, other unmeasurable types UNSUPPORTED, and anything that would give
an unreliable number FAILED, with a message.
"""

import math
from dataclasses import dataclass

import shapely
from pyproj import CRS
from pyproj.exceptions import CRSError, ProjError

from app.core.enums import MeasurementStatus, MeasurementType
from app.services.crs_service import CrsResolutionError, CrsResolver
from app.services.geospatial_processor import ExtractedFeature, ParsedDataset

AREA_UNIT = "m²"
LENGTH_UNIT = "m"

_MEASUREMENT_TYPES = {
    "Polygon": MeasurementType.AREA,
    "MultiPolygon": MeasurementType.AREA,
    "LineString": MeasurementType.LENGTH,
    "MultiLineString": MeasurementType.LENGTH,
}
_POINT_TYPES = frozenset({"Point", "MultiPoint"})
_MISSING_CRS = (
    "The file does not define a coordinate reference system (for a Shapefile, the .prj file "
    "is missing), so areas and lengths cannot be computed reliably."
)
_UNUSABLE_CRS = "The file's coordinate reference system cannot be used for measurement."


@dataclass(frozen=True)
class MeasurementResult:
    measurement_type: MeasurementType
    status: MeasurementStatus
    value: float | None = None
    unit: str | None = None
    crs: str | None = None  # the projected CRS the value was computed in
    error_message: str | None = None


def measure_dataset(dataset: ParsedDataset) -> list[MeasurementResult]:
    """One result per feature, in feature order."""
    resolver, crs_problem = _resolver_for(dataset.crs)
    return [measure_feature(feature, resolver, crs_problem) for feature in dataset.features]


def measure_feature(
    feature: ExtractedFeature, resolver: CrsResolver | None, crs_problem: str | None = None
) -> MeasurementResult:
    geometry_type = feature.geometry_type
    if geometry_type in _POINT_TYPES:
        return MeasurementResult(MeasurementType.NONE, MeasurementStatus.NOT_APPLICABLE)
    if geometry_type is None:
        return _failed(MeasurementType.NONE, "The feature has no geometry.")
    measurement_type = _MEASUREMENT_TYPES.get(geometry_type)
    if measurement_type is None:
        return MeasurementResult(
            MeasurementType.NONE,
            MeasurementStatus.UNSUPPORTED,
            error_message=f"Measurement is not supported for {geometry_type} geometries.",
        )

    geometry = feature.geometry
    if geometry is None:  # present in the file, but unparseable or non-finite
        return _failed(measurement_type, feature.geometry_error or "The geometry is unusable.")
    if geometry.is_empty:
        return _failed(measurement_type, "The geometry is empty.")
    if not geometry.is_valid:
        # A bow-tie polygon has no meaningful area, and make_valid can change the shape being
        # measured, so invalid geometries are reported instead of fixed.
        return _failed(
            measurement_type,
            f"The geometry is invalid ({shapely.is_valid_reason(geometry)}); "
            "invalid geometries are not repaired automatically.",
        )
    if resolver is None:
        return _failed(measurement_type, crs_problem or _MISSING_CRS)

    try:
        projection = resolver.resolve(geometry, measurement_type)
        projected = projection.project(geometry)
    except CrsResolutionError as exc:
        return _failed(measurement_type, str(exc))

    # Planar (2D) measures in the projected CRS, converted from its unit to metres.
    if measurement_type is MeasurementType.AREA:
        value, unit = projected.area * projection.metres_per_unit**2, AREA_UNIT
    else:
        value, unit = projected.length * projection.metres_per_unit, LENGTH_UNIT
    if not math.isfinite(value):
        return _failed(measurement_type, "The measurement produced a non-finite value.")
    return MeasurementResult(
        measurement_type, MeasurementStatus.CALCULATED, value, unit, projection.crs_code
    )


def _resolver_for(crs: CRS | None) -> tuple[CrsResolver | None, str | None]:
    if crs is None:
        return None, _MISSING_CRS
    try:
        return CrsResolver(crs), None
    except (CRSError, ProjError):  # e.g. an engineering CRS with no geodetic datum
        return None, _UNUSABLE_CRS


def _failed(measurement_type: MeasurementType, message: str) -> MeasurementResult:
    return MeasurementResult(measurement_type, MeasurementStatus.FAILED, error_message=message)
