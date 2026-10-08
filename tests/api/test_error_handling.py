import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.repositories.file_repository import FileRepository
from app.services.file_service import FileService


def test_database_failure_on_read_returns_503_without_details(
    offline_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*_: object) -> None:
        raise OperationalError("SELECT files.id ...", {}, ConnectionRefusedError("refused"))

    monkeypatch.setattr(FileRepository, "get", unavailable)

    response = offline_client.get(f"/api/v1/files/{uuid.uuid4()}")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DATABASE_ERROR",
            "message": "The database is unavailable or could not complete the request.",
        }
    }


def test_unexpected_error_returns_500_without_leaking_internals(
    offline_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*_: object) -> None:
        raise RuntimeError("secret detail at C:\\internal\\path.py")

    monkeypatch.setattr(FileService, "get_file", crash)

    response = offline_client.get(f"/api/v1/files/{uuid.uuid4()}")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred."}
    }
    assert "secret" not in response.text
    assert len(response.headers["X-Request-ID"]) == 32
