import io
from pathlib import Path

import pytest
from pyogrio.errors import DataLayerError
from pyproj import CRS
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from app.core.exceptions import FileTooLargeError, MalformedFileError
from app.services.file_validation import StagedUpload, UploadLimits, stage_upload
from app.services.geospatial_processor import ParsedDataset, _dataset_crs, read_features
from tests.factories import FIXTURES_DIR, kml_document, placemark, shapefile_zip

LIMITS = UploadLimits(
    max_upload_bytes=10 * 1024 * 1024,
    max_uncompressed_bytes=10 * 1024 * 1024,
    max_archive_members=20,
)
SQUARE_UTM = Polygon(
    [(500_000, 1_400_000), (500_100, 1_400_000), (500_100, 1_400_200), (500_000, 1_400_200)]
)


def stage(data: bytes, filename: str, workdir: Path) -> StagedUpload:
    return stage_upload(io.BytesIO(data), filename, workdir, LIMITS)


def read(data: bytes, filename: str, workdir: Path, max_features: int = 1000) -> ParsedDataset:
    return read_features(stage(data, filename, workdir), max_features)


# --- KML ------------------------------------------------------------------------------------


@pytest.fixture
def survey(tmp_path: Path) -> ParsedDataset:
    return read((FIXTURES_DIR / "site_survey.kml").read_bytes(), "site_survey.kml", tmp_path)


def test_kml_features_are_read_from_every_folder_in_order(survey: ParsedDataset) -> None:
    assert [(f.index, f.layer_name, f.properties["Name"]) for f in survey.features] == [
        (0, "Site survey", "Control point"),
        (1, "Boundaries", "Plot A"),
        (2, "Boundaries", "Plot B"),
        (3, "Courtyards", "Courtyard plot"),  # nested folder
        (4, "Roads", "Access road"),
        (5, "Roads", "Drainage"),
        (6, "Markers", "Boreholes"),
        (7, "Markers", "Site office"),
        (8, "Markers", "Planned well"),
    ]


def test_kml_geometry_types_are_identified(survey: ParsedDataset) -> None:
    assert [feature.geometry_type for feature in survey.features] == [
        "Point",
        "Polygon",
        "MultiPolygon",
        "Polygon",
        "LineString",
        "MultiLineString",
        "MultiPoint",
        "GeometryCollection",
        None,  # placemark without geometry
    ]


def test_kml_crs_is_wgs84(survey: ParsedDataset) -> None:
    assert survey.crs == CRS.from_epsg(4326)


def test_kml_attributes_include_extended_data_but_not_presentation_fields(
    survey: ParsedDataset,
) -> None:
    assert survey.features[1].properties == {
        "Name": "Plot A",
        "description": "Survey boundary",
        "owner": "Acme Infra",
        "plot_no": "42",
    }
    assert survey.features[0].properties == {"id": "control-1", "Name": "Control point"}


def test_kml_geometry_is_exposed_as_geojson_in_source_crs(survey: ParsedDataset) -> None:
    point, polygon_with_hole = survey.features[0], survey.features[3]

    assert point.geometry_as_geojson() == {
        "type": "Point",
        "coordinates": (77.5946, 12.9716, 920.0),
    }
    geojson = polygon_with_hole.geometry_as_geojson()
    assert geojson is not None
    assert geojson["type"] == "Polygon"
    assert len(geojson["coordinates"]) == 2  # exterior ring and one hole


def test_feature_without_geometry_is_kept(survey: ParsedDataset) -> None:
    planned = survey.features[8]

    assert planned.geometry is None
    assert planned.geometry_error is None
    assert planned.geometry_as_geojson() is None


def test_root_level_placemark_layer_is_named_after_the_uploaded_file(tmp_path: Path) -> None:
    document = (
        b'<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><name>solo</name>'
        b"<Point><coordinates>10,20</coordinates></Point></Placemark></kml>"
    )

    dataset = read(document, "field_notes.kml", tmp_path)

    assert [feature.layer_name for feature in dataset.features] == ["field_notes"]


def test_kml_without_placemarks_has_no_features(tmp_path: Path) -> None:
    assert read(kml_document("<name>empty</name>"), "empty.kml", tmp_path).features == []


@pytest.mark.filterwarnings("ignore:Non closed ring detected:RuntimeWarning")
@pytest.mark.filterwarnings("ignore:invalid value encountered:RuntimeWarning")
def test_unusable_geometries_are_isolated_per_feature(tmp_path: Path) -> None:
    data = (FIXTURES_DIR / "invalid_geometries.kml").read_bytes()

    bowtie, unclosed, corrupt, valid = read(data, "qa.kml", tmp_path).features

    assert bowtie.geometry is not None
    assert not bowtie.geometry.is_valid  # parsed; validity is judged when measuring
    assert unclosed.geometry is None
    assert unclosed.geometry_type == "Polygon"  # recovered from the WKB header
    assert unclosed.geometry_error is not None
    assert "do not form a closed linestring" in unclosed.geometry_error
    assert corrupt.geometry is None
    assert corrupt.geometry_type == "LineString"
    assert (
        corrupt.geometry_error == "The geometry contains non-finite coordinates (NaN or infinity)."
    )
    assert valid.geometry == Point(77.59, 12.97)


def test_kml_that_gdal_cannot_open_is_malformed(tmp_path: Path) -> None:
    # A <kml> root with no Document or Placemark passes validation, but GDAL refuses it.
    with pytest.raises(MalformedFileError, match="KML file could not be read"):
        read(b'<kml xmlns="http://www.opengis.net/kml/2.2"></kml>', "bare.kml", tmp_path)


def test_gdal_read_error_is_reported_as_malformed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_read(*_: object, **__: object) -> None:
        raise DataLayerError("GDAL detail that must not reach clients")

    monkeypatch.setattr("app.services.geospatial_processor.raw.read", failing_read)

    with pytest.raises(MalformedFileError, match=r"^The Shapefile could not be read\.$"):
        read(shapefile_zip([Point(1, 1)]), "points.zip", tmp_path)


def test_layers_with_different_crs_are_rejected() -> None:
    # A file-level CRS must describe every feature; GDAL never mixes CRSs for KML/Shapefile.
    with pytest.raises(MalformedFileError, match="different coordinate reference systems"):
        _dataset_crs([CRS.from_epsg(4326), CRS.from_epsg(4269)])


def test_truncated_kml_is_malformed(tmp_path: Path) -> None:
    truncated = kml_document(placemark("a", "<Point><coordinates>1,2</coordinates></Point>"))[:-20]

    with pytest.raises(MalformedFileError, match="KML file could not be read"):
        read(truncated, "broken.kml", tmp_path)


def test_kml_feature_limit_is_enforced_across_folders(tmp_path: Path) -> None:
    data = (FIXTURES_DIR / "site_survey.kml").read_bytes()

    with pytest.raises(FileTooLargeError, match="more than 5 features"):
        read(data, "site_survey.kml", tmp_path, max_features=5)


# --- Shapefile ------------------------------------------------------------------------------


def test_shapefile_features_crs_and_typed_attributes_are_read(tmp_path: Path) -> None:
    archive = shapefile_zip(
        [SQUARE_UTM, None, MultiPolygon([SQUARE_UTM, SQUARE_UTM.buffer(-10)])],
        stem="parcels",
        crs="EPSG:32643",
        fields={
            "plot_id": [1, 2, 3],
            "area_ha": [2.0, float("nan"), 1.5],
            "owner": ["Ånand Rao", None, "Meera"],
            "surveyed": ["2024-01-15", "NaT", "2023-12-31"],
            "active": [True, False, True],
        },
    )

    dataset = read(archive, "parcels.zip", tmp_path)

    assert dataset.crs == CRS.from_epsg(32643)
    assert [f.geometry_type for f in dataset.features] == ["Polygon", None, "MultiPolygon"]
    assert {f.layer_name for f in dataset.features} == {"parcels"}
    assert dataset.features[0].properties == {
        "plot_id": 1,
        "area_ha": 2.0,
        "owner": "Ånand Rao",
        "surveyed": "2024-01-15",
        "active": True,
    }
    # Missing values become JSON nulls rather than NaN/NaT.
    assert dataset.features[1].properties["area_ha"] is None
    assert dataset.features[1].properties["owner"] is None


def test_shapefile_without_prj_has_unknown_crs(tmp_path: Path) -> None:
    archive = shapefile_zip([LineString([(0, 0), (1, 1)])], exclude=[".prj"])

    assert read(archive, "lines.zip", tmp_path).crs is None


def test_shapefile_with_unidentified_custom_crs_is_still_parsed(tmp_path: Path) -> None:
    custom = "+proj=tmerc +lat_0=0 +lon_0=77.5 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m"
    archive = shapefile_zip([Point(10, 10)], crs=custom)

    crs = read(archive, "custom.zip", tmp_path).crs

    assert crs is not None
    assert crs.is_projected
    assert crs.to_authority() is None


def test_shapefile_feature_limit_is_enforced(tmp_path: Path) -> None:
    archive = shapefile_zip([Point(i, i) for i in range(4)])

    with pytest.raises(FileTooLargeError, match="more than 3 features"):
        read(archive, "points.zip", tmp_path, max_features=3)
