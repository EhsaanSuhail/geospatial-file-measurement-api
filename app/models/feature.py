import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Feature(Base):
    """A feature extracted from a file, stored as GeoJSON in the file's source CRS."""

    __tablename__ = "features"
    __table_args__ = (
        # Also serves as the index for "features of a file, in file order".
        UniqueConstraint("file_id", "feature_index"),
        CheckConstraint("feature_index >= 0", name="feature_index_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    feature_index: Mapped[int]
    # KML folder / Shapefile name the feature came from.
    layer_name: Mapped[str] = mapped_column(Text)
    # NULL when the source feature has no geometry.
    geometry_type: Mapped[str | None] = mapped_column(String(32))
    # NULL when there is no geometry or it cannot be represented (unparseable, NaN coordinates).
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Why `geometry` is NULL although the source feature had one.
    geometry_error: Mapped[str | None] = mapped_column(Text)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
