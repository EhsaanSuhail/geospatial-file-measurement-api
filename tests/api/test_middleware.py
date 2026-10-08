from fastapi.testclient import TestClient


def test_unknown_route_uses_error_envelope(offline_client: TestClient) -> None:
    response = offline_client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found"}}


def test_wrong_method_uses_error_envelope(offline_client: TestClient) -> None:
    response = offline_client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_request_id_is_generated_when_absent(offline_client: TestClient) -> None:
    response = offline_client.get("/does-not-exist")

    assert len(response.headers["X-Request-ID"]) == 32


def test_well_formed_request_id_is_propagated(offline_client: TestClient) -> None:
    response = offline_client.get("/does-not-exist", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


def test_malformed_request_id_is_replaced(offline_client: TestClient) -> None:
    response = offline_client.get(
        "/does-not-exist", headers={"X-Request-ID": "bad id\nwith injected log line"}
    )

    assert response.headers["X-Request-ID"] != "bad id\nwith injected log line"
    assert len(response.headers["X-Request-ID"]) == 32


def test_trailing_slash_is_routed_without_redirect(offline_client: TestClient) -> None:
    response = offline_client.get("/openapi.json/", follow_redirects=False)

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "GeoMeasure API"


def test_declared_oversized_body_is_rejected_before_reading(offline_client: TestClient) -> None:
    oversized = b"x" * (2 * 1024 * 1024)  # offline_client allows 1 MB uploads

    response = offline_client.post("/health", content=oversized)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
