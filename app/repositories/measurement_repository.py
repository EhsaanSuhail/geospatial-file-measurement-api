import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.core.enums import MeasurementStatus
from app.models import Feature, Measurement


@dataclass(frozen=True)
class FeatureMeasurement:
    """A measurement with the identifying details of the feature it belongs to."""

    measurement: Measurement
    feature_index: int
    geometry_type: str | None


class MeasurementRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_all(self, rows: Sequence[dict[str, Any]]) -> None:
        if rows:
            self._session.execute(insert(Measurement), rows)

    def count_by_status(self, file_id: uuid.UUID) -> dict[MeasurementStatus, int]:
        statement = (
            select(Measurement.status, func.count())
            .join(Feature, Measurement.feature_id == Feature.id)
            .where(Feature.file_id == file_id)
            .group_by(Measurement.status)
        )
        counts = dict.fromkeys(MeasurementStatus, 0)
        counts.update({status: count for status, count in self._session.execute(statement)})
        return counts

    def list_for_file(
        self, file_id: uuid.UUID, limit: int, offset: int
    ) -> list[FeatureMeasurement]:
        statement = (
            select(Measurement, Feature.feature_index, Feature.geometry_type)
            .join(Feature, Measurement.feature_id == Feature.id)
            .where(Feature.file_id == file_id)
            .order_by(Feature.feature_index, Measurement.measurement_type)
            .limit(limit)
            .offset(offset)
        )
        return [
            FeatureMeasurement(measurement, feature_index, geometry_type)
            for measurement, feature_index, geometry_type in self._session.execute(statement)
        ]
