import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


def test_health_is_ok_when_database_is_reachable(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_unavailable_when_database_is_unreachable(
    offline_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_connection(_: Session) -> None:
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))

    monkeypatch.setattr("app.api.routes.health.check_database_connection", refuse_connection)

    response = offline_client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unavailable"}
