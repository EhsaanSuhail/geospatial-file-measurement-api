"""Transaction boundaries and failure handling of the upload pipeline (against PostgreSQL)."""

import contextlib
import io
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import FileStatus
from app.core.exceptions import (
    DatabaseError,
    MalformedFileError,
    ProcessingError,
    UnsupportedFileFormatError,
)
from app.models import Feature, FileRecord, Measurement
from app.repositories.file_repository import FileRepository
from app.repositories.measurement_repository import MeasurementRepository
from app.services.file_service import FileService
from tests.factories import FIXTURES_DIR, kml_document, placemark

SURVEY_KML = (FIXTURES_DIR / "site_survey.kml").read_bytes()


def database_unavailable(*_: object, **__: object) -> None:
    raise OperationalError("INSERT ...", {}, ConnectionResetError("server closed the connection"))


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def only_file(session: Session) -> FileRecord:
    session.expire_all()
    return session.scalars(select(FileRecord)).one()


@pytest.fixture
def service(db_session: Session, settings: Settings) -> FileService:
    return FileService(db_session, settings)


def test_successful_upload_stores_all_results_and_completes(
    service: FileService, db_session: Session
) -> None:
    record = service.upload(io.BytesIO(SURVEY_KML), "site_survey.kml")

    assert record.status is FileStatus.COMPLETED
    assert record.feature_count == 9
    assert record.original_crs == "EPSG:4326"
    assert count(db_session, Feature) == 9
    assert count(db_session, Measurement) == 9


def test_rejected_upload_leaves_no_record(service: FileService, db_session: Session) -> None:
    with pytest.raises(UnsupportedFileFormatError):
        service.upload(io.BytesIO(b"data"), "notes.txt")

    assert count(db_session, FileRecord) == 0


def test_persistence_failure_rolls_back_results_and_marks_file_failed(
    service: FileService, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Features are inserted first; the measurement insert then fails in the same transaction.
    monkeypatch.setattr(MeasurementRepository, "add_all", database_unavailable)

    with pytest.raises(DatabaseError) as raised:
        service.upload(io.BytesIO(SURVEY_KML), "site_survey.kml")

    record = only_file(db_session)
    assert raised.value.details == {"file_id": str(record.id)}
    assert record.status is FileStatus.FAILED  # never COMPLETED without its results
    assert record.error_message == "The processing results could not be saved."
    assert count(db_session, Feature) == 0  # the features inserted before the failure are gone


def test_unexpected_error_marks_file_failed_with_a_generic_message(
    service: FileService, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("internal detail that must not reach clients")

    monkeypatch.setattr("app.services.file_service.measure_dataset", explode)

    with pytest.raises(ProcessingError) as raised:
        service.upload(io.BytesIO(SURVEY_KML), "site_survey.kml")

    record = only_file(db_session)
    assert record.status is FileStatus.FAILED
    assert record.error_message == "An unexpected error occurred while processing the file."
    assert "internal detail" not in raised.value.message


def test_unreadable_content_marks_file_failed_with_the_reason(
    service: FileService, db_session: Session
) -> None:
    truncated = kml_document(placemark("a", "<Point><coordinates>1,2</coordinates></Point>"))[:-20]

    with pytest.raises(MalformedFileError) as raised:
        service.upload(io.BytesIO(truncated), "broken.kml")

    record = only_file(db_session)
    assert raised.value.details["file_id"] == str(record.id)
    assert (record.status, record.error_message) == (
        FileStatus.FAILED,
        "The KML file could not be read.",
    )


def test_original_error_is_raised_even_if_the_failure_cannot_be_recorded(
    service: FileService, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    truncated = kml_document(placemark("a", "<Point><coordinates>1,2</coordinates></Point>"))[:-20]
    monkeypatch.setattr(FileRepository, "mark_failed", database_unavailable)

    with pytest.raises(MalformedFileError):
        service.upload(io.BytesIO(truncated), "broken.kml")

    # The database "went away": the record is left PROCESSING, never falsely COMPLETED.
    assert only_file(db_session).status is FileStatus.PROCESSING


@pytest.mark.parametrize(
    ("data", "filename"),
    [(SURVEY_KML, "site_survey.kml"), (b"not a zip", "bad.zip")],
    ids=["success", "failure"],
)
def test_temporary_files_are_always_removed(
    service: FileService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: bytes,
    filename: str,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    with contextlib.suppress(MalformedFileError):
        service.upload(io.BytesIO(data), filename)

    assert list(tmp_path.iterdir()) == []
