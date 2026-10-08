"""Runs an upload from start to finish, and answers the read endpoints.

How the database is used:

1. Validation writes nothing, so a rejected upload leaves no record.
2. The file is saved as PROCESSING first, so every accepted upload can be found.
3. Reading and measuring happen outside any transaction.
4. Features, measurements and the COMPLETED status are saved in one transaction, so a file is
   never COMPLETED with missing results.
5. If anything fails, that transaction is rolled back and the file is marked FAILED.

`_process` does not depend on the request, so it could be moved to a background worker later.
"""

import logging
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import FileStatus, MeasurementStatus
from app.core.exceptions import AppError, DatabaseError, FileRecordNotFoundError, ProcessingError
from app.models import Feature, FileRecord
from app.repositories.feature_repository import FeatureRepository
from app.repositories.file_repository import FileRepository
from app.repositories.measurement_repository import FeatureMeasurement, MeasurementRepository
from app.services.crs_service import describe_crs
from app.services.file_validation import StagedUpload, UploadLimits, stage_upload
from app.services.geospatial_processor import ParsedDataset, read_features
from app.services.measurement_service import MeasurementResult, measure_dataset

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeaturePage:
    file: FileRecord
    total: int
    items: list[Feature]


@dataclass(frozen=True)
class MeasurementPage:
    file: FileRecord
    total: int
    summary: dict[MeasurementStatus, int]
    items: list[FeatureMeasurement]


class FileService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._files = FileRepository(session)
        self._features = FeatureRepository(session)
        self._measurements = MeasurementRepository(session)
        self._limits = UploadLimits(
            max_upload_bytes=settings.max_upload_size_bytes,
            max_uncompressed_bytes=settings.max_uncompressed_size_bytes,
            max_archive_members=settings.max_archive_members,
        )
        self._max_features = settings.max_feature_count

    def upload(self, stream: IO[bytes], filename: str | None) -> FileRecord:
        """Validate, store and process an upload synchronously. Raises AppError subclasses."""
        with tempfile.TemporaryDirectory(prefix="geomeasure-", ignore_cleanup_errors=True) as tmp:
            staged = stage_upload(stream, filename, Path(tmp), self._limits)
            record = self._files.add(
                FileRecord(
                    filename=staged.filename,
                    file_format=staged.file_format,
                    file_size_bytes=staged.size_bytes,
                    status=FileStatus.PROCESSING,
                    feature_count=0,
                )
            )
            self._session.commit()
            logger.info(
                "File accepted",
                extra={"file_id": str(record.id), "file_format": staged.file_format},
            )
            self._process(record, staged)
        return record

    def get_file(self, file_id: uuid.UUID) -> FileRecord:
        record = self._files.get(file_id)
        if record is None:
            raise FileRecordNotFoundError(f"File {file_id} does not exist.")
        return record

    def list_features(self, file_id: uuid.UUID, limit: int, offset: int) -> FeaturePage:
        record = self.get_file(file_id)
        return FeaturePage(
            file=record,
            # Saved in the same transaction as the features, so no COUNT query is needed.
            total=record.feature_count,
            items=self._features.list_for_file(file_id, limit, offset),
        )

    def list_measurements(self, file_id: uuid.UUID, limit: int, offset: int) -> MeasurementPage:
        record = self.get_file(file_id)
        summary = self._measurements.count_by_status(file_id)
        return MeasurementPage(
            file=record,
            total=sum(summary.values()),
            summary=summary,
            items=self._measurements.list_for_file(file_id, limit, offset),
        )

    def _process(self, record: FileRecord, staged: StagedUpload) -> None:
        file_id = record.id  # read before any rollback expires the object's attributes
        started = time.perf_counter()
        try:
            dataset = read_features(staged, self._max_features)
            results = measure_dataset(dataset)
            self._store_results(record, dataset, results)
            self._session.commit()
        except AppError as exc:
            self._mark_failed(file_id, exc.message)
            exc.details["file_id"] = str(file_id)
            raise
        except SQLAlchemyError as exc:
            logger.exception("Could not store processing results", extra={"file_id": str(file_id)})
            message = "The processing results could not be saved."
            self._mark_failed(file_id, message)
            raise DatabaseError(message, details={"file_id": str(file_id)}) from exc
        except Exception as exc:
            logger.exception("Unexpected processing error", extra={"file_id": str(file_id)})
            message = "An unexpected error occurred while processing the file."
            self._mark_failed(file_id, message)
            raise ProcessingError(message, details={"file_id": str(file_id)}) from exc

        statuses = Counter(result.status for result in results)
        logger.info(
            "File processed",
            extra={
                "file_id": str(file_id),
                "feature_count": record.feature_count,
                "measurements": {status.value: count for status, count in statuses.items()},
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )

    def _store_results(
        self, record: FileRecord, dataset: ParsedDataset, results: list[MeasurementResult]
    ) -> None:
        feature_rows: list[dict[str, Any]] = []
        measurement_rows: list[dict[str, Any]] = []
        for feature, result in zip(dataset.features, results, strict=True):
            feature_id = uuid.uuid4()  # generated here so measurements can reference it
            feature_rows.append(
                {
                    "id": feature_id,
                    "file_id": record.id,
                    "feature_index": feature.index,
                    "layer_name": feature.layer_name,
                    "geometry_type": feature.geometry_type,
                    "geometry": feature.geometry_as_geojson(),
                    "geometry_error": feature.geometry_error,
                    "properties": feature.properties,
                }
            )
            measurement_rows.append(
                {
                    "id": uuid.uuid4(),
                    "feature_id": feature_id,
                    "measurement_type": result.measurement_type,
                    "value": result.value,
                    "unit": result.unit,
                    "measurement_crs": result.crs,
                    "status": result.status,
                    "error_message": result.error_message,
                }
            )
        self._features.add_all(feature_rows)
        self._measurements.add_all(measurement_rows)
        record.status = FileStatus.COMPLETED
        record.feature_count = len(feature_rows)
        record.original_crs = describe_crs(dataset.crs) if dataset.crs is not None else None
        record.error_message = None

    def _mark_failed(self, file_id: uuid.UUID, error_message: str) -> None:
        """Discard partial results, then record the failure in a transaction of its own."""
        self._session.rollback()
        try:
            self._files.mark_failed(file_id, error_message)
            self._session.commit()
        except SQLAlchemyError:
            # Usually the database is down; the file stays PROCESSING.
            self._session.rollback()
            logger.exception("Could not mark file as FAILED", extra={"file_id": str(file_id)})
