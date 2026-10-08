import io
from pathlib import Path

import numpy as np
import pytest
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.geometry.base import BaseGeometry

from app.core.enums import MeasurementStatus, MeasurementType
from app.services.crs_service import CrsResolver
from app.services.file_validation import UploadLimits, stage_upload
from app.services.geospatial_processor import ExtractedFeature, ParsedDataset, read_features
from app.services.measurement_service import (
    AREA_UNIT,
    LENGTH_UNIT,
    MeasurementResult,
    measure_dataset,
    measure_feature,
)
from tests.factories import FIXTURES_DIR

WGS84 = CRS.from_epsg(4326)
UTM_43N = CRS.from_epsg(32643)  # Bengaluru
GEOD = Geod(ellps="WGS84")
E0, N0 = 500_000.0, 1_400_000.0  # on UTM 43N's central meridian


def feature(geometry: BaseGeometry | None, error: str | None = None) -> ExtractedFeature:
    geometry_type = None if geometry is None else geometry.geom_type
    return ExtractedFeature(0, "layer", geometry_type, geometry, {}, error)


def measure(geometry: BaseGeometry, crs: CRS = WGS84) -> MeasurementResult:
    return measure_feature(feature(geometry), CrsResolver(crs))


def from_utm_43n(geometry: BaseGeometry) -> BaseGeometry:
    transformer = Transformer.from_crs(UTM_43N, WGS84, always_xy=True)
    return shapely.transform(
        geometry, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1]))
    )


def geodesic_area(polygon: BaseGeometry) -> float:
    """Ground truth on the WGS 84 ellipsoid.

    Geod sums signed ring areas, so holes must wind opposite to the exterior or they are added
    instead of subtracted. (GEOS planar area, used by the service, is orientation-independent.)
    """
    return abs(GEOD.geometry_area_perimeter(shapely.orient_polygons(polygon))[0])


# --- exact values in a projected CRS --------------------------------------------------------


def test_polygon_area_in_metric_projected_crs_is_exact() -> None:
    result = measure(box(E0, N0, E0 + 100, N0 + 200), UTM_43N)

    assert result == MeasurementResult(
        MeasurementType.AREA, MeasurementStatus.CALCULATED, 20_000.0, AREA_UNIT, "EPSG:32643"
    )


def test_polygon_holes_are_excluded_from_area() -> None:
    courtyard = Polygon(
        box(E0, N0, E0 + 100, N0 + 100).exterior.coords,
        [box(E0 + 40, N0 + 40, E0 + 60, N0 + 60).exterior.coords],
    )

    assert measure(courtyard, UTM_43N).value == pytest.approx(10_000 - 400, rel=1e-12)


def test_linestring_length_in_metric_projected_crs_is_exact() -> None:
    road = LineString([(E0, N0), (E0 + 300, N0 + 400), (E0 + 300, N0 + 1000)])  # 500 m + 600 m

    result = measure(road, UTM_43N)

    assert result.measurement_type is MeasurementType.LENGTH
    assert result.value == pytest.approx(1100.0, rel=1e-12)
    assert result.unit == LENGTH_UNIT == "m"


def test_multipart_geometries_sum_their_parts() -> None:
    plots = MultiPolygon([box(E0, N0, E0 + 10, N0 + 10), box(E0 + 20, N0, E0 + 30, N0 + 20)])
    drains = MultiLineString([[(E0, N0), (E0 + 30, N0)], [(E0, N0 + 5), (E0, N0 + 45)]])

    assert measure(plots, UTM_43N).value == pytest.approx(100 + 200, rel=1e-12)
    assert measure(drains, UTM_43N).value == pytest.approx(30 + 40, rel=1e-12)


def test_us_survey_feet_are_converted_to_metres() -> None:
    feet = 1200 / 3937  # metres per US survey foot
    x, y = Transformer.from_crs(WGS84, 2263, always_xy=True).transform(-73.985, 40.705)

    area = measure(box(x, y, x + 1000, y + 1000), CRS.from_epsg(2263))
    length = measure(LineString([(x, y), (x + 1000, y)]), CRS.from_epsg(2263))

    assert area.crs == "EPSG:2263"
    assert area.value == pytest.approx(1_000_000 * feet**2, rel=1e-12)
    assert length.value == pytest.approx(1000 * feet, rel=1e-12)


def test_measurements_are_planimetric() -> None:
    sloped_road = LineString([(E0, N0, 0.0), (E0 + 300, N0 + 400, 1200.0)])

    assert measure(sloped_road, UTM_43N).value == pytest.approx(500.0, rel=1e-12)


# --- geographic input: projected before measuring -------------------------------------------


def test_known_one_square_kilometre_defined_in_utm_measures_correctly_from_wgs84() -> None:
    square_km = from_utm_43n(box(E0, N0, E0 + 1000, N0 + 1000))  # vertices in degrees

    result = measure(square_km)

    assert result.crs == "EPSG:32643"
    assert result.value == pytest.approx(1_000_000.0, rel=1e-9)
    assert result.unit == "m²"


def test_known_one_kilometre_line_defined_in_utm_measures_correctly_from_wgs84() -> None:
    assert measure(from_utm_43n(LineString([(E0, N0), (E0, N0 + 1000)]))).value == pytest.approx(
        1000.0, rel=1e-9
    )


@pytest.mark.parametrize(
    ("polygon", "tolerance"),
    [
        (box(13.40, 52.50, 13.41, 52.51), 0.001),  # Berlin, near its zone's central meridian
        (box(151.20, -33.87, 151.21, -33.86), 0.001),  # Sydney, southern hemisphere
        (box(77.59, 12.97, 77.60, 12.98), 0.0025),  # Bengaluru, 2.6 degrees from the meridian
        (box(11.9, 0.0, 12.1, 0.1), 0.005),  # straddling a zone edge on the equator
        (box(77.55, -0.05, 77.65, 0.05), 0.0025),  # straddling the equator (hemispheres)
        (Polygon([(179.9, -16.0), (-179.9, -16.0), (-179.9, -16.1), (179.9, -16.1)]), 0.0025),
        (box(0.0, 88.0, 10.0, 88.5), 0.0025),  # beyond UTM's nominal 84°N limit
    ],
    ids=["berlin", "sydney", "bengaluru", "zone-edge", "equator", "antimeridian", "arctic"],
)
def test_area_from_wgs84_agrees_with_geodesic_area(polygon: Polygon, tolerance: float) -> None:
    result = measure(polygon)

    assert result.status is MeasurementStatus.CALCULATED
    assert result.value == pytest.approx(geodesic_area(polygon), rel=tolerance)


def test_length_from_wgs84_agrees_with_geodesic_length() -> None:
    line = LineString([(-74.00, 40.70), (-73.99, 40.71), (-73.98, 40.70)])

    assert measure(line).value == pytest.approx(GEOD.geometry_length(line), rel=0.001)


def to_crs(geometry: BaseGeometry, target: int) -> BaseGeometry:
    transformer = Transformer.from_crs(WGS84, target, always_xy=True)
    return shapely.transform(
        geometry, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1]))
    )


def test_web_mercator_input_is_measured_like_wgs84_input() -> None:
    berlin = box(13.40, 52.50, 13.41, 52.51)  # Web Mercator's own "area" here is 2.7x too large

    result = measure(to_crs(berlin, 3857), CRS.from_epsg(3857))

    assert result.crs == "EPSG:32633"
    assert result.value == pytest.approx(geodesic_area(berlin), rel=0.001)


def test_state_plane_input_is_measured_in_its_own_crs_more_accurately_than_utm() -> None:
    # Areal scale error here: -0.0004% in NY Long Island (US survey feet), -0.06% in UTM 18N,
    # so the 0.01% tolerance below would fail if the feature were reprojected to UTM.
    manhattan = box(-73.99, 40.70, -73.98, 40.71)

    result = measure(to_crs(manhattan, 2263), CRS.from_epsg(2263))

    assert result.crs == "EPSG:2263"
    assert result.value == pytest.approx(geodesic_area(manhattan), rel=0.0001)


def test_degrees_are_never_used_as_units() -> None:
    one_hundredth_degree = box(77.59, 12.97, 77.60, 12.98)

    value = measure(one_hundredth_degree).value

    assert value is not None
    assert value > 1_000_000  # ~1.2 km², not 0.0001 "square degrees"


# --- non-measured and failing cases ---------------------------------------------------------


@pytest.mark.parametrize(
    "geometry", [Point(77.59, 12.97), MultiPoint([(77.59, 12.97), (77.6, 13)])]
)
def test_points_need_no_measurement(geometry: BaseGeometry) -> None:
    assert measure(geometry) == MeasurementResult(
        MeasurementType.NONE, MeasurementStatus.NOT_APPLICABLE
    )


def test_geometry_collection_is_unsupported() -> None:
    mixed = GeometryCollection([Point(77.59, 12.97), LineString([(77.59, 12.97), (77.6, 12.98)])])

    result = measure(mixed)

    assert result.status is MeasurementStatus.UNSUPPORTED
    assert result.measurement_type is MeasurementType.NONE
    assert result.value is None
    assert result.error_message == "Measurement is not supported for GeometryCollection geometries."


def test_invalid_polygon_fails_instead_of_returning_a_wrong_area() -> None:
    bowtie = Polygon([(77.59, 12.97), (77.60, 12.98), (77.60, 12.97), (77.59, 12.98)])
    assert bowtie.area < 1e-12  # the shoelace formula cancels the two triangles out

    result = measure(bowtie)

    assert result.status is MeasurementStatus.FAILED
    assert result.measurement_type is MeasurementType.AREA
    assert result.value is None
    assert result.error_message is not None
    assert "Self-intersection" in result.error_message


def test_empty_geometry_fails() -> None:
    result = measure(Polygon())

    assert (result.status, result.error_message) == (
        MeasurementStatus.FAILED,
        "The geometry is empty.",
    )


def test_feature_without_geometry_fails() -> None:
    result = measure_feature(feature(None), CrsResolver(WGS84))

    assert result == MeasurementResult(
        MeasurementType.NONE, MeasurementStatus.FAILED, error_message="The feature has no geometry."
    )


def test_unparseable_geometry_reports_its_parse_error() -> None:
    unusable = ExtractedFeature(
        0, "layer", "Polygon", None, {}, "The geometry could not be parsed."
    )

    result = measure_feature(unusable, CrsResolver(WGS84))

    assert result.measurement_type is MeasurementType.AREA
    assert result.error_message == "The geometry could not be parsed."


def test_missing_crs_fails_measurable_features_but_not_points() -> None:
    dataset = ParsedDataset(
        crs=None,
        features=[feature(box(0, 0, 10, 10)), feature(Point(1, 1))],
    )

    area, point = measure_dataset(dataset)

    assert area.status is MeasurementStatus.FAILED
    assert area.error_message is not None
    assert ".prj" in area.error_message
    assert point.status is MeasurementStatus.NOT_APPLICABLE


def test_crs_without_geodetic_datum_fails_explicitly() -> None:
    local_grid = CRS.from_wkt(
        'ENGCRS["Site grid",EDATUM["Local"],CS[Cartesian,2],AXIS["x",east],AXIS["y",north],'
        'LENGTHUNIT["metre",1]]'
    )

    (result,) = measure_dataset(ParsedDataset(local_grid, [feature(box(0, 0, 10, 10))]))

    assert result.status is MeasurementStatus.FAILED
    assert (
        result.error_message
        == "The file's coordinate reference system cannot be used for measurement."
    )


def test_feature_too_large_for_a_local_projection_fails() -> None:
    result = measure(box(0, 0, 40, 1))

    assert result.status is MeasurementStatus.FAILED
    assert result.error_message is not None
    assert "too large" in result.error_message


# --- whole dataset --------------------------------------------------------------------------


def test_every_feature_of_the_survey_fixture_is_measured(tmp_path: Path) -> None:
    staged = stage_upload(
        io.BytesIO((FIXTURES_DIR / "site_survey.kml").read_bytes()),
        "site_survey.kml",
        tmp_path,
        UploadLimits(10**7, 10**7, 10),
    )
    dataset = read_features(staged, max_features=100)

    results = measure_dataset(dataset)

    assert [(r.measurement_type, r.status) for r in results] == [
        (MeasurementType.NONE, MeasurementStatus.NOT_APPLICABLE),  # Point
        (MeasurementType.AREA, MeasurementStatus.CALCULATED),  # Polygon
        (MeasurementType.AREA, MeasurementStatus.CALCULATED),  # MultiPolygon
        (MeasurementType.AREA, MeasurementStatus.CALCULATED),  # Polygon with a hole
        (MeasurementType.LENGTH, MeasurementStatus.CALCULATED),  # LineString
        (MeasurementType.LENGTH, MeasurementStatus.CALCULATED),  # MultiLineString
        (MeasurementType.NONE, MeasurementStatus.NOT_APPLICABLE),  # MultiPoint
        (MeasurementType.NONE, MeasurementStatus.UNSUPPORTED),  # GeometryCollection
        (MeasurementType.NONE, MeasurementStatus.FAILED),  # no geometry
    ]
    for extracted, result in zip(dataset.features, results, strict=True):
        if result.status is MeasurementStatus.CALCULATED:
            assert result.crs == "EPSG:32643"
            geodesic = (
                geodesic_area(extracted.geometry)
                if result.measurement_type is MeasurementType.AREA
                else GEOD.geometry_length(extracted.geometry)
            )
            assert result.value == pytest.approx(geodesic, rel=0.0025)
