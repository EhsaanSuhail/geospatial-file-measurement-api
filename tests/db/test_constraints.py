"""The database itself enforces the rules the services rely on, not only the ORM."""

import uuid

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import FileFormat, FileStatus, MeasurementStatus, MeasurementType
from app.models import Feature, FileRecord, Measurement


def _file(session: Session) -> FileRecord:
    record = FileRecord(
        filename="site.kml",
        file_format=FileFormat.KML,
        file_size_bytes=10,
        status=FileStatus.PROCESSING,
    )
    session.add(record)
    session.flush()
    return record


def _feature(session: Session, file_id: uuid.UUID, index: int = 0) -> Feature:
    feature = Feature(
        file_id=file_id,
        feature_index=index,
        layer_name="site",
        geometry_type="Point",
        properties={},
    )
    session.add(feature)
    session.flush()
    return feature


def test_timestamps_are_populated_by_the_database(db_session: Session) -> None:
    record = _file(db_session)
    db_session.commit()

    assert record.created_at is not None
    assert record.updated_at == record.created_at


def test_updated_at_is_refreshed_on_update_without_lazy_load(db_session: Session) -> None:
    record = _file(db_session)
    db_session.commit()
    created_at = record.created_at

    record.status = FileStatus.COMPLETED
    db_session.commit()

    assert "updated_at" not in inspect(record).unloaded  # fetched via RETURNING
    assert record.updated_at > created_at


def test_feature_index_is_unique_within_a_file(db_session: Session) -> None:
    record = _file(db_session)
    _feature(db_session, record.id, index=0)

    with pytest.raises(IntegrityError, match="uq_features_file_id_feature_index"):
        _feature(db_session, record.id, index=0)


def test_calculated_measurement_requires_a_value(db_session: Session) -> None:
    feature = _feature(db_session, _file(db_session).id)
    db_session.add(
        Measurement(
            feature_id=feature.id,
            measurement_type=MeasurementType.AREA,
            status=MeasurementStatus.CALCULATED,
            value=None,
        )
    )

    with pytest.raises(IntegrityError, match="value_present_iff_calculated"):
        db_session.flush()


def test_measurement_value_cannot_be_negative(db_session: Session) -> None:
    feature = _feature(db_session, _file(db_session).id)
    db_session.add(
        Measurement(
            feature_id=feature.id,
            measurement_type=MeasurementType.LENGTH,
            status=MeasurementStatus.CALCULATED,
            value=-1.0,
            unit="m",
            measurement_crs="EPSG:32643",
        )
    )

    with pytest.raises(IntegrityError, match="value_non_negative"):
        db_session.flush()


def test_deleting_a_file_cascades_to_features_and_measurements(db_session: Session) -> None:
    record = _file(db_session)
    feature = _feature(db_session, record.id)
    db_session.add(
        Measurement(
            feature_id=feature.id,
            measurement_type=MeasurementType.NONE,
            status=MeasurementStatus.NOT_APPLICABLE,
        )
    )
    db_session.commit()

    db_session.delete(record)
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(Feature)) == 0
    assert db_session.scalar(select(func.count()).select_from(Measurement)) == 0
