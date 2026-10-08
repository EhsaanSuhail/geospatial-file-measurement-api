import numpy as np
import pytest
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from app.core.enums import MeasurementType
from app.services.crs_service import (
    CrsResolutionError,
    CrsResolver,
    MeasurementProjection,
    describe_crs,
    spherical_centre,
    utm_epsg_code,
)

AREA, LENGTH = MeasurementType.AREA, MeasurementType.LENGTH
BERLIN = box(13.40, 52.50, 13.41, 52.51)


def to_crs(geometry: BaseGeometry, target: int | str) -> BaseGeometry:
    transformer = Transformer.from_crs("EPSG:4326", target, always_xy=True)
    return shapely.transform(
        geometry, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1]))
    )


def resolve(source: int | str, geometry: BaseGeometry, kind=AREA) -> MeasurementProjection:
    return CrsResolver(CRS.from_user_input(source)).resolve(geometry, kind)


# --- UTM zone selection ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (13.4, 52.5, 32633),  # Berlin
        (77.59, 12.97, 32643),  # Bengaluru
        (-74.0, 40.7, 32618),  # New York
        (151.2, -33.87, 32756),  # Sydney, southern hemisphere
        (0.0, 0.0, 32631),  # the equator belongs to the northern zones
        (0.0, -0.0001, 32731),
        (-180.0, 10.0, 32601),
        (179.99, 10.0, 32660),
        (180.0, 10.0, 32601),  # wraps
        (190.0, 10.0, 32602),  # 0-360 longitudes wrap too
    ],
)
def test_utm_zone_selection(lon: float, lat: float, expected: int) -> None:
    assert utm_epsg_code(lon, lat) == expected


def test_spherical_centre_handles_the_antimeridian() -> None:
    lon, lat = spherical_centre(np.array([179.9, -179.9]), np.array([-16.0, -16.0]))

    assert abs(lon) == pytest.approx(180.0)  # a naive mean of degrees would give 0
    assert lat == pytest.approx(-16.0, abs=1e-3)


# --- geographic source CRS ------------------------------------------------------------------


def test_geographic_feature_is_projected_to_its_utm_zone() -> None:
    projection = resolve(4326, BERLIN)

    assert projection.crs_code == "EPSG:32633"
    assert projection.metres_per_unit == 1.0
    assert projection.transformer is not None


def test_transformation_uses_longitude_latitude_order() -> None:
    # EPSG:4326 officially orders axes latitude-first; file coordinates are longitude-first.
    # Without always_xy, (13.4, 52.5) would be read as latitude 13.4: thousands of km away.
    projected = resolve(4326, BERLIN).project(Point(13.4, 52.5))

    assert projected.x == pytest.approx(391_390.73, abs=0.01)
    assert projected.y == pytest.approx(5_817_855.24, abs=0.01)


def test_projection_that_cannot_represent_a_geometry_raises() -> None:
    utm_33n = MeasurementProjection(
        "EPSG:32633", 1.0, Transformer.from_crs(4326, 32633, always_xy=True)
    )

    with pytest.raises(CrsResolutionError, match="could not be transformed to EPSG:32633"):
        utm_33n.project(Point(105.0, 0.0))  # 90 degrees from the meridian, on the equator


def test_projected_geometry_is_two_dimensional() -> None:
    line_z = LineString([(13.40, 52.50, 120.0), (13.41, 52.51, 80.0)])

    assert not resolve(4326, line_z, LENGTH).project(line_z).has_z


def test_non_wgs84_geographic_crs_with_grads_and_paris_meridian_is_handled() -> None:
    # NTF (Paris): grads, prime meridian through Paris. A point in Paris must still map to UTM 31N.
    paris = Transformer.from_crs(4326, 4807, always_xy=True).transform(2.35, 48.85)
    feature = box(paris[0], paris[1], paris[0] + 0.001, paris[1] + 0.001)

    assert resolve(4807, feature).crs_code == "EPSG:32631"


def test_feature_crossing_the_antimeridian_uses_a_zone_next_to_it() -> None:
    fiji = Polygon([(179.9, -16.0), (-179.9, -16.0), (-179.9, -16.1), (179.9, -16.1)])

    projection = resolve(4326, fiji)

    assert projection.crs_code in {"EPSG:32701", "EPSG:32760"}
    width = projection.project(fiji).bounds
    assert width[2] - width[0] == pytest.approx(21_400, rel=0.01)  # ~0.2 degrees, not ~360


def test_polar_feature_is_measurable() -> None:
    assert resolve(4326, box(0, 88, 10, 88.5)).crs_code == "EPSG:32631"


def test_feature_too_large_for_one_zone_is_rejected() -> None:
    with pytest.raises(CrsResolutionError, match=r"too large .* would distort it up to"):
        resolve(4326, box(0, 0, 40, 1))


# UTM 33N's central meridian is 15°E. At the equator its scale error is 0.231% (linear) and
# 0.463% (areal) 4.2° from the meridian, and 0.271% / 0.543% at 4.5°: just inside and just
# outside the 0.25% and 0.5% limits.
@pytest.mark.parametrize(("half_width", "accepted"), [(4.2, True), (4.5, False)])
def test_length_tolerance_is_a_quarter_percent(half_width: float, accepted: bool) -> None:
    line = LineString([(15 - half_width, 0.0), (15 + half_width, 0.0)])

    if accepted:
        assert resolve(4326, line, LENGTH).crs_code == "EPSG:32633"
    else:
        with pytest.raises(CrsResolutionError, match=r"up to 0\.27% \(limit 0\.25%\)"):
            resolve(4326, line, LENGTH)


@pytest.mark.parametrize(("half_width", "accepted"), [(4.2, True), (4.5, False)])
def test_area_tolerance_is_half_a_percent(half_width: float, accepted: bool) -> None:
    strip = box(15 - half_width, 0.0, 15 + half_width, 0.01)

    if accepted:
        assert resolve(4326, strip, AREA).crs_code == "EPSG:32633"
    else:
        with pytest.raises(CrsResolutionError, match=r"up to 0\.54% \(limit 0\.50%\)"):
            resolve(4326, strip, AREA)


def test_every_vertex_is_checked_even_across_the_antimeridian() -> None:
    # In longitude/latitude the convex hull of this line is (-179.5, 0)-(179.5, 0): its true
    # western end (176°E) lies "inside" it, yet has 0.72% scale error in the chosen zone.
    line = LineString([(176.0, 0.0), (179.5, 0.0), (-179.5, 0.0), (-176.0, 0.0)])

    with pytest.raises(CrsResolutionError, match=r"up to 0\.72% \(limit 0\.25%\)"):
        resolve(4326, line, LENGTH)


def test_coordinates_outside_geographic_range_are_rejected() -> None:
    # A typical error: projected coordinates in a file whose .prj says EPSG:4326.
    with pytest.raises(CrsResolutionError, match=r"outside the valid range .*\(EPSG:4326\)"):
        resolve(4326, box(500_000, 1_400_000, 500_100, 1_400_200))


# --- projected source CRS -------------------------------------------------------------------


def test_suitable_projected_crs_is_used_directly() -> None:
    projection = resolve(32643, box(500_000, 1_400_000, 500_100, 1_400_200))

    assert projection == MeasurementProjection("EPSG:32643", 1.0)
    assert projection.transformer is None


def test_projected_crs_in_us_survey_feet_is_used_with_unit_conversion() -> None:
    projection = resolve(2263, to_crs(box(-73.99, 40.70, -73.98, 40.71), 2263))

    assert projection.crs_code == "EPSG:2263"
    assert projection.metres_per_unit == pytest.approx(1200 / 3937)  # US survey foot


def test_projected_crs_far_outside_its_zone_is_replaced() -> None:
    lisbon_in_zone_33 = to_crs(box(-9.0, 39.0, -8.99, 39.01), 32633)

    assert resolve(32633, lisbon_in_zone_33).crs_code == "EPSG:32629"


@pytest.mark.parametrize(
    ("area", "utm_zone"),
    [(box(0.5, 0.5, 0.51, 0.51), "EPSG:32631"), (BERLIN, "EPSG:32633")],
    ids=["equator", "berlin"],
)
def test_web_mercator_is_never_used_directly(area: Polygon, utm_zone: str) -> None:
    # At the equator PROJ reports no Web Mercator distortion, so only the method check stops it.
    assert resolve(3857, to_crs(area, 3857)).crs_code == utm_zone


def test_projected_crs_with_non_greenwich_prime_meridian_is_evaluated_correctly() -> None:
    # MGI (Ferro) / Austria GK West: longitudes count from Ferro, 17.67° west of Greenwich.
    # Evaluated with Greenwich longitudes, Innsbruck would appear 17.67° off the CRS's meridian.
    innsbruck = to_crs(box(11.39, 47.26, 11.40, 47.27), 31251)

    assert resolve(31251, innsbruck).crs_code == "EPSG:31251"


def test_unidentified_projected_crs_is_replaced_by_utm() -> None:
    custom = "+proj=tmerc +lat_0=0 +lon_0=77.5 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m"

    assert resolve(custom, box(0, 1_400_000, 100, 1_400_100)).crs_code == "EPSG:32643"


def test_equal_area_crs_is_accepted_for_area_but_not_for_length() -> None:
    feature = to_crs(BERLIN, 6933)  # WGS 84 / NSIDC EASE-Grid 2.0 Global (equal-area)

    assert resolve(6933, feature, AREA).crs_code == "EPSG:6933"
    assert resolve(6933, feature.exterior, LENGTH).crs_code == "EPSG:32633"


# --- reporting ------------------------------------------------------------------------------


def test_crs_is_described_by_authority_code_or_wkt() -> None:
    assert describe_crs(CRS.from_epsg(4326)) == "EPSG:4326"
    custom = describe_crs(CRS.from_proj4("+proj=tmerc +lon_0=77.5 +ellps=WGS84 +units=m"))
    assert custom.startswith("PROJCRS[")
