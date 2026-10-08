import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pyproj import Geod
from shapely.geometry import Polygon, shape
from sqlalchemy import Engine, func, select

from app.core.config import Settings
from app.main import create_app
from app.models import Feature, FileRecord
from tests.factories import FIXTURES_DIR, build_zip, kml_document, placemark, shapefile_zip

FILES = "/api/v1/files"
SURVEY_KML = (FIXTURES_DIR / "site_survey.kml").read_bytes()
FILE_FIELDS = {
    "id",
    "filename",
    "format",
    "file_size_bytes",
    "crs",
    "feature_count",
    "status",
    "error_message",
    "created_at",
    "updated_at",
}
MEASUREMENT_FIELDS = {
    "feature_id",
    "feature_index",
    "geometry_type",
    "measurement_type",
    "value",
    "unit",
    "measurement_crs",
    "status",
    "error_message",
}
UTM_PLOT = Polygon(
    [(500_000, 1_400_000), (500_100, 1_400_000), (500_100, 1_400_200), (500_000, 1_400_200)]
)


def upload(client: TestClient, data: bytes, filename: str, path: str = FILES):
    return client.post(path, files={"file": (filename, data, "application/octet-stream")})


def count_rows(engine: Engine, model: type) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def survey(client: TestClient) -> dict:
    response = upload(client, SURVEY_KML, "site_survey.kml")
    assert response.status_code == 201, response.text
    return response.json()


# --- upload ---------------------------------------------------------------------------------


def test_upload_kml_returns_file_metadata(client: TestClient, survey: dict) -> None:
    assert set(survey) == FILE_FIELDS
    assert survey["filename"] == "site_survey.kml"
    assert survey["format"] == "KML"
    assert survey["crs"] == "EPSG:4326"
    assert survey["feature_count"] == 9
    assert survey["status"] == "COMPLETED"
    assert survey["error_message"] is None
    assert survey["file_size_bytes"] == len(SURVEY_KML)
    uuid.UUID(survey["id"])


def test_upload_sets_location_of_the_new_file(client: TestClient) -> None:
    response = upload(client, SURVEY_KML, "site_survey.kml")

    assert response.headers["Location"].endswith(f"{FILES}/{response.json()['id']}")


def test_upload_shapefile_zip_in_projected_crs(client: TestClient) -> None:
    neighbour = Polygon([(500_200, 1_400_000), (500_300, 1_400_000), (500_300, 1_400_100)])
    archive = shapefile_zip([UTM_PLOT, neighbour], stem="parcels", crs="EPSG:32643")

    response = upload(client, archive, "parcels.zip")

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["format"], body["crs"], body["feature_count"]) == ("SHAPEFILE", "EPSG:32643", 2)


def test_upload_shapefile_without_prj_completes_but_cannot_measure(client: TestClient) -> None:
    archive = shapefile_zip([UTM_PLOT], exclude=[".prj"])

    created = upload(client, archive, "no_crs.zip").json()
    (item,) = client.get(f"{FILES}/{created['id']}/measurements").json()["items"]

    assert (created["status"], created["crs"]) == ("COMPLETED", None)
    assert (item["status"], item["value"]) == ("FAILED", None)
    assert ".prj" in item["error_message"]


@pytest.mark.parametrize("filename", ["notes.txt", "site.geojson", "site.kmz"])
def test_upload_unsupported_format_is_rejected(
    client: TestClient, db_engine: Engine, filename: str
) -> None:
    response = upload(client, b"irrelevant", filename)

    assert response.status_code == 415
    assert response.json() == {
        "error": {
            "code": "UNSUPPORTED_FILE_FORMAT",
            "message": "Only KML files (.kml) and ZIP archives containing a Shapefile (.zip) "
            "are supported.",
        }
    }
    assert count_rows(db_engine, FileRecord) == 0  # rejected before any record is created


def test_upload_malformed_zip_is_rejected(client: TestClient, db_engine: Engine) -> None:
    response = upload(client, b"this is not a zip archive", "parcels.zip")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MALFORMED_FILE"
    assert count_rows(db_engine, FileRecord) == 0


def test_upload_invalid_shapefile_archive_is_rejected(client: TestClient) -> None:
    response = upload(client, shapefile_zip([UTM_PLOT], exclude=[".shx"]), "parcels.zip")

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "INVALID_SHAPEFILE_ARCHIVE",
        "message": "The Shapefile is missing required component(s): .shx.",
    }


def test_upload_kml_with_wrong_content_is_rejected(client: TestClient) -> None:
    response = upload(client, build_zip({"doc.kml": SURVEY_KML}), "site.kml")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MALFORMED_FILE"


def test_upload_without_file_is_an_invalid_request(client: TestClient) -> None:
    response = client.post(FILES, data={"not_file": "x"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert error["details"]["errors"][0]["location"] == "body.file"


def test_content_that_fails_after_acceptance_marks_the_file_failed(client: TestClient) -> None:
    # Passes the cheap root-element check, then fails in GDAL: the record must say FAILED.
    truncated = kml_document(placemark("a", "<Point><coordinates>1,2</coordinates></Point>"))[:-20]

    response = upload(client, truncated, "broken.kml")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "MALFORMED_FILE"
    record = client.get(f"{FILES}/{error['details']['file_id']}").json()
    assert record["status"] == "FAILED"
    assert record["error_message"] == "The KML file could not be read."
    assert record["feature_count"] == 0


def test_error_responses_do_not_leak_internals(client: TestClient) -> None:
    truncated = kml_document(placemark("a", "<Point><coordinates>1,2</coordinates></Point>"))[:-20]

    text = upload(client, truncated, "broken.kml").text

    for leak in ("Traceback", "geomeasure-", "upload-", "pyogrio", "GDAL", "\\", "/tmp"):
        assert leak not in text


@pytest.fixture
def small_limit_client(settings: Settings, clean_database: None) -> Iterator[TestClient]:
    limited = settings.model_copy(update={"max_upload_size_mb": 1, "max_feature_count": 3})
    with TestClient(create_app(limited)) as test_client:
        yield test_client


def test_upload_over_size_limit_is_rejected(small_limit_client: TestClient) -> None:
    response = upload(small_limit_client, b" " * (2 * 1024 * 1024), "big.kml")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_chunked_upload_over_size_limit_is_rejected(small_limit_client: TestClient) -> None:
    boundary = "geomeasure-boundary"

    def multipart_body() -> Iterator[bytes]:  # streamed: no Content-Length header is sent
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.kml"'
            "\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).encode()
        for _ in range(3):
            yield b" " * (1024 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()

    response = small_limit_client.post(
        FILES,
        content=multipart_body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_file_over_feature_limit_is_rejected_and_marked_failed(
    small_limit_client: TestClient,
) -> None:
    response = upload(small_limit_client, SURVEY_KML, "site_survey.kml")  # 9 features, limit 3

    assert response.status_code == 413
    file_id = response.json()["error"]["details"]["file_id"]
    assert small_limit_client.get(f"{FILES}/{file_id}").json()["status"] == "FAILED"


# --- file metadata --------------------------------------------------------------------------


def test_get_file_returns_metadata(client: TestClient, survey: dict) -> None:
    response = client.get(f"{FILES}/{survey['id']}")

    assert response.status_code == 200
    assert response.json() == survey


def test_get_nonexistent_file_is_not_found(client: TestClient) -> None:
    missing = uuid.uuid4()

    response = client.get(f"{FILES}/{missing}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "FILE_NOT_FOUND", "message": f"File {missing} does not exist."}
    }


def test_get_file_with_malformed_id_is_an_invalid_request(client: TestClient) -> None:
    response = client.get(f"{FILES}/abc123")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


# --- measurements ---------------------------------------------------------------------------


def test_measurements_are_reported_per_feature(client: TestClient, survey: dict) -> None:
    response = client.get(f"{FILES}/{survey['id']}/measurements")

    assert response.status_code == 200
    body = response.json()
    assert (body["file_id"], body["file_status"], body["total"]) == (survey["id"], "COMPLETED", 9)
    assert body["summary"] == {"calculated": 5, "not_applicable": 2, "unsupported": 1, "failed": 1}
    assert [item["feature_index"] for item in body["items"]] == list(range(9))
    assert all(set(item) == MEASUREMENT_FIELDS for item in body["items"])

    plot_a = body["items"][1]
    assert plot_a["geometry_type"] == "Polygon"
    assert (plot_a["measurement_type"], plot_a["status"]) == ("AREA", "CALCULATED")
    assert (plot_a["unit"], plot_a["measurement_crs"]) == ("m²", "EPSG:32643")
    access_road = body["items"][4]
    assert (access_road["measurement_type"], access_road["unit"]) == ("LENGTH", "m")
    collection = body["items"][7]
    assert (collection["status"], collection["value"]) == ("UNSUPPORTED", None)


def test_measured_values_match_geodesic_ground_truth(client: TestClient, survey: dict) -> None:
    geod = Geod(ellps="WGS84")
    features = client.get(f"{FILES}/{survey['id']}/features").json()["items"]
    measurements = client.get(f"{FILES}/{survey['id']}/measurements").json()["items"]

    plot_a = shape(features[1]["geometry"])
    access_road = shape(features[4]["geometry"])

    assert measurements[1]["value"] == pytest.approx(
        abs(geod.geometry_area_perimeter(plot_a)[0]), rel=0.0025
    )
    assert measurements[4]["value"] == pytest.approx(geod.geometry_length(access_road), rel=0.0025)


def test_shapefile_in_projected_crs_is_measured_without_reprojection(client: TestClient) -> None:
    archive = shapefile_zip([UTM_PLOT], crs="EPSG:32643")
    file_id = upload(client, archive, "parcels.zip").json()["id"]

    (item,) = client.get(f"{FILES}/{file_id}/measurements").json()["items"]

    assert item["value"] == pytest.approx(20_000.0, rel=1e-12)
    assert item["measurement_crs"] == "EPSG:32643"


def test_measurements_are_paginated_with_a_whole_file_summary(
    client: TestClient, survey: dict
) -> None:
    body = client.get(
        f"{FILES}/{survey['id']}/measurements", params={"limit": 2, "offset": 3}
    ).json()

    assert (body["total"], body["limit"], body["offset"]) == (9, 2, 3)
    assert [item["feature_index"] for item in body["items"]] == [3, 4]
    assert sum(body["summary"].values()) == 9


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 5000}, {"offset": -1}])
def test_invalid_pagination_is_rejected(client: TestClient, survey: dict, params: dict) -> None:
    response = client.get(f"{FILES}/{survey['id']}/measurements", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_measurements_of_nonexistent_file_are_not_found(client: TestClient) -> None:
    response = client.get(f"{FILES}/{uuid.uuid4()}/measurements")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


# --- features -------------------------------------------------------------------------------


def test_features_expose_index_type_geometry_crs_and_properties(
    client: TestClient, survey: dict
) -> None:
    body = client.get(f"{FILES}/{survey['id']}/features").json()

    assert body["total"] == 9
    plot_a = body["items"][1]
    assert plot_a["feature_index"] == 1
    assert plot_a["layer"] == "Boundaries"
    assert plot_a["geometry_type"] == "Polygon"
    assert plot_a["crs"] == "EPSG:4326"
    assert plot_a["geometry"]["type"] == "Polygon"
    assert plot_a["geometry"]["coordinates"][0][0] == [77.59, 12.97]
    assert plot_a["properties"] == {
        "Name": "Plot A",
        "description": "Survey boundary",
        "owner": "Acme Infra",
        "plot_no": "42",
    }
    assert body["items"][8]["geometry"] is None  # placemark without geometry


@pytest.mark.filterwarnings("ignore:Non closed ring detected:RuntimeWarning")
@pytest.mark.filterwarnings("ignore:invalid value encountered:RuntimeWarning")
def test_unusable_geometries_are_explained_on_the_feature(client: TestClient) -> None:
    data = (FIXTURES_DIR / "invalid_geometries.kml").read_bytes()
    file_id = upload(client, data, "qa.kml").json()["id"]

    features = client.get(f"{FILES}/{file_id}/features").json()["items"]
    measurements = client.get(f"{FILES}/{file_id}/measurements").json()["items"]

    assert [m["status"] for m in measurements] == ["FAILED", "FAILED", "FAILED", "NOT_APPLICABLE"]
    assert "Self-intersection" in measurements[0]["error_message"]
    assert features[1]["geometry"] is None
    assert "closed linestring" in features[1]["geometry_error"]
    assert features[2]["geometry_error"] == measurements[2]["error_message"]


def test_upload_with_unparseable_multipart_body_is_an_invalid_request(client: TestClient) -> None:
    response = client.post(
        FILES, content=b"no boundary", headers={"Content-Type": "multipart/form-data"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_failed_file_has_no_measurements(client: TestClient) -> None:
    truncated = kml_document(placemark("a", "<Point><coordinates>1,2</coordinates></Point>"))[:-20]
    file_id = upload(client, truncated, "broken.kml").json()["error"]["details"]["file_id"]

    body = client.get(f"{FILES}/{file_id}/measurements").json()

    assert (body["file_status"], body["total"], body["items"]) == ("FAILED", 0, [])
    assert body["summary"] == {"calculated": 0, "not_applicable": 0, "unsupported": 0, "failed": 0}


def test_failed_file_has_no_features(client: TestClient, db_engine: Engine) -> None:
    truncated = kml_document(placemark("a", "<Point><coordinates>1,2</coordinates></Point>"))[:-20]
    file_id = upload(client, truncated, "broken.kml").json()["error"]["details"]["file_id"]

    body = client.get(f"{FILES}/{file_id}/features").json()

    assert (body["file_status"], body["total"], body["items"]) == ("FAILED", 0, [])
    assert count_rows(db_engine, Feature) == 0


# --- paths from the assignment brief --------------------------------------------------------


def test_unversioned_paths_with_trailing_slashes_from_the_brief_work(client: TestClient) -> None:
    created = upload(client, SURVEY_KML, "survey.kml", path="/api/files/")
    assert created.status_code == 201
    file_id = created.json()["id"]

    file_info = client.get(f"/api/files/{file_id}/", follow_redirects=False)
    measurements = client.get(f"/api/files/{file_id}/measurements/", follow_redirects=False)

    assert file_info.status_code == 200
    assert file_info.json()["status"] == "COMPLETED"
    assert measurements.status_code == 200
    assert measurements.json()["total"] == 9
