"""Pick the projected CRS each feature is measured in.

Areas and lengths are never calculated in degrees. If the file already uses a suitable projected
CRS, the feature is measured in it; otherwise it is converted to the UTM zone of its centre.
Either way, PROJ's scale factors are checked at every vertex, and a feature that would be
distorted too much fails instead of getting a wrong value.
"""

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from pyproj import CRS, Proj, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely.geometry.base import BaseGeometry

from app.core.enums import MeasurementType

# UTM itself is off by up to about 0.1% inside a zone. These limits allow that plus some overlap
# into the next zone, and reject anything more distorted.
MAX_LINEAR_SCALE_ERROR = 0.0025  # 0.25 %
MAX_AREAL_SCALE_ERROR = 0.005  # 0.5 %

_WGS84 = CRS.from_epsg(4326)
# Web Mercator uses sphere formulas on ellipsoid coordinates, so it is ~0.7% off even at the
# equator, and PROJ's scale factors don't show it. It is excluded by its method name instead.
_WEB_MERCATOR_METHOD = "Popular Visualisation Pseudo Mercator"


class CrsResolutionError(Exception):
    """No CRS can measure the feature reliably. The message is safe to show to clients."""


@dataclass(frozen=True)
class MeasurementProjection:
    crs_code: str  # authority code, e.g. "EPSG:32643"
    metres_per_unit: float
    # None when features are already in this CRS (the source CRS is used directly).
    transformer: Transformer | None = field(default=None, compare=False)

    def project(self, geometry: BaseGeometry) -> BaseGeometry:
        """Return a 2D copy of `geometry` in this CRS (coordinates in its native unit)."""
        if self.transformer is None:
            return geometry
        transform = self.transformer.transform
        projected = shapely.transform(
            geometry, lambda xy: np.column_stack(transform(xy[:, 0], xy[:, 1]))
        )
        if not np.isfinite(shapely.get_coordinates(projected)).all():
            raise CrsResolutionError(f"The geometry could not be transformed to {self.crs_code}.")
        return projected


@dataclass(frozen=True)
class _Candidate:
    projection: MeasurementProjection
    proj: Proj  # evaluates the CRS's scale factors at given longitudes/latitudes
    # PROJ reads those longitudes relative to the CRS's own prime meridian (e.g. Paris, Ferro).
    prime_meridian: float = 0.0  # degrees east of Greenwich


class CrsResolver:
    """Chooses the measurement CRS for each feature of one file.

    Create one per file: it caches PROJ objects, which are slow to build. Not thread-safe.
    """

    def __init__(self, source_crs: CRS) -> None:
        self._source_crs = source_crs
        self._to_wgs84 = Transformer.from_crs(source_crs, _WGS84, always_xy=True)
        self._direct = _direct_candidate(source_crs)
        self._utm: dict[int, _Candidate] = {}

    def resolve(
        self, geometry: BaseGeometry, measurement_type: MeasurementType
    ) -> MeasurementProjection:
        """Pick the CRS to measure a non-empty geometry in, or raise CrsResolutionError."""
        limit = _limit(measurement_type)
        lon, lat = self._vertices_lonlat(geometry)
        direct = self._direct
        if direct is not None and _scale_error(direct, lon, lat, measurement_type) <= limit:
            return direct.projection

        candidate = self._utm_candidate(utm_epsg_code(*spherical_centre(lon, lat)))
        error = _scale_error(candidate, lon, lat, measurement_type)
        if error > limit:
            extent = f"up to {error:.2%}" if math.isfinite(error) else "beyond its valid area"
            raise CrsResolutionError(
                "The feature is too large to be measured accurately in a single projected CRS: "
                f"{candidate.projection.crs_code} would distort it {extent} "
                f"(limit {limit:.2%})."
            )
        return candidate.projection

    def _vertices_lonlat(self, geometry: BaseGeometry) -> tuple[np.ndarray, np.ndarray]:
        """WGS 84 longitude/latitude of every vertex.

        A convex hull is not enough: in longitude/latitude, the hull of a feature that crosses
        ±180° leaves out its real eastern and western ends.
        """
        xy = shapely.get_coordinates(geometry)
        lon, lat = (np.asarray(values) for values in self._to_wgs84.transform(xy[:, 0], xy[:, 1]))
        if not (np.isfinite(lon).all() and np.isfinite(lat).all()) or (
            np.abs(lat).max() > 90 or np.abs(lon).max() > 360
        ):
            raise CrsResolutionError(
                "The coordinates are outside the valid range of the declared CRS "
                f"({describe_crs(self._source_crs)}); the file's CRS may be wrong."
            )
        return lon, lat

    def _utm_candidate(self, epsg_code: int) -> _Candidate:
        if epsg_code not in self._utm:
            utm = CRS.from_epsg(epsg_code)
            transformer = Transformer.from_crs(self._source_crs, utm, always_xy=True)
            self._utm[epsg_code] = _Candidate(
                MeasurementProjection(f"EPSG:{epsg_code}", 1.0, transformer), Proj(utm)
            )
        return self._utm[epsg_code]


def utm_epsg_code(lon: float, lat: float) -> int:
    """EPSG code of the WGS 84 / UTM zone containing a point (32601-32660 N, 32701-32760 S).

    The special zones around Norway and Svalbard are ignored on purpose: they exist for map
    sheets, and the regular zone keeps the feature closer to the zone's central meridian.
    """
    zone = int((lon + 180) // 6) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def spherical_centre(lon: np.ndarray, lat: np.ndarray) -> tuple[float, float]:
    """Average position of the distinct points, calculated on a sphere.

    Averaging degrees would put the centre of a feature crossing ±180° on the wrong side of the
    world. Duplicates (like the closing vertex of each ring) are removed first.
    """
    points = np.unique(lon + 1j * lat)  # much faster than np.unique(..., axis=0)
    lon_rad, lat_rad = np.radians(points.real), np.radians(points.imag)
    x = float(np.mean(np.cos(lat_rad) * np.cos(lon_rad)))
    y = float(np.mean(np.cos(lat_rad) * np.sin(lon_rad)))
    z = float(np.mean(np.sin(lat_rad)))
    return math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))


def authority_code(crs: CRS) -> str | None:
    authority = crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else None


def describe_crs(crs: CRS) -> str:
    """Authority code when PROJ can identify the CRS, its WKT otherwise."""
    return authority_code(crs) or crs.to_wkt()


def _direct_candidate(crs: CRS) -> _Candidate | None:
    """The source CRS, if it may be measured in directly (still subject to distortion checks)."""
    horizontal = crs.sub_crs_list[0] if crs.is_compound else crs
    if not horizontal.is_projected:
        return None
    operation = horizontal.coordinate_operation
    if operation is not None and operation.method_name == _WEB_MERCATOR_METHOD:
        return None
    # Only CRSs with an EPSG/authority code, so the reported measurement CRS can be looked up.
    code = authority_code(horizontal)
    metres_per_unit = horizontal.axis_info[0].unit_conversion_factor if horizontal.axis_info else 0
    if code is None or not (math.isfinite(metres_per_unit) and metres_per_unit > 0):
        return None
    try:
        proj = Proj(horizontal)
    except (CRSError, ProjError):
        return None
    meridian = horizontal.prime_meridian
    prime_meridian = (
        math.degrees(meridian.longitude * float(meridian.unit_conversion_factor))
        if meridian is not None
        else 0.0
    )
    return _Candidate(MeasurementProjection(code, metres_per_unit), proj, prime_meridian)


def _limit(measurement_type: MeasurementType) -> float:
    if measurement_type is MeasurementType.AREA:
        return MAX_AREAL_SCALE_ERROR
    if measurement_type is MeasurementType.LENGTH:
        return MAX_LINEAR_SCALE_ERROR
    raise ValueError(f"{measurement_type} is not a measurement that needs a projection")


def _scale_error(
    candidate: _Candidate, lon: np.ndarray, lat: np.ndarray, measurement_type: MeasurementType
) -> float:
    """Largest scale error at the given points (inf where the CRS is not defined).

    Areas only need the areal scale, so equal-area projections always pass for areas. Lengths
    need the scale in every direction, given by the Tissot ellipse's semi-axes.
    """
    factors = candidate.proj.get_factors(lon - candidate.prime_meridian, lat)
    if measurement_type is MeasurementType.AREA:
        errors = np.abs(np.asarray(factors.areal_scale) - 1)
    else:
        errors = np.maximum(
            np.abs(np.asarray(factors.tissot_semimajor) - 1),
            np.abs(np.asarray(factors.tissot_semiminor) - 1),
        )
    return float(errors.max()) if np.isfinite(errors).all() else math.inf
