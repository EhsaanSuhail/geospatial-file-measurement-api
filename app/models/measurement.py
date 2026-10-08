import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import MeasurementStatus, MeasurementType
from app.db.base import Base, enum_column_type


class Measurement(Base):
    """A measurement derived from a feature. Kept apart from features so that derived values
    can be recomputed or extended (e.g. perimeter) without touching the source data."""

    __tablename__ = "measurements"
    __table_args__ = (
        UniqueConstraint("feature_id", "measurement_type"),
        CheckConstraint(
            "(status = 'CALCULATED') = (value IS NOT NULL)", name="value_present_iff_calculated"
        ),
        CheckConstraint("value >= 0", name="value_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    feature_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("features.id", ondelete="CASCADE"))
    measurement_type: Mapped[MeasurementType] = mapped_column(
        enum_column_type(MeasurementType, "measurement_type")
    )
    value: Mapped[float | None] = mapped_column(Double)
    unit: Mapped[str | None] = mapped_column(String(16))
    # Authority code of the projected CRS the value was computed in, e.g. "EPSG:32643".
    measurement_crs: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[MeasurementStatus] = mapped_column(
        enum_column_type(MeasurementStatus, "measurement_status")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
