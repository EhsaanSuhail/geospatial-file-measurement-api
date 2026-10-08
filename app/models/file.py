import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import FileFormat, FileStatus
from app.db.base import Base, enum_column_type


class FileRecord(Base):
    """One uploaded file and its processing outcome."""

    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("file_size_bytes >= 0", name="file_size_bytes_non_negative"),
        CheckConstraint("feature_count >= 0", name="feature_count_non_negative"),
    )
    # Fetch server-generated timestamps (also `updated_at` on UPDATE) via RETURNING, so objects
    # returned after a commit are complete without a lazy reload.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 - SQLAlchemy declarative hook

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[FileFormat] = mapped_column(enum_column_type(FileFormat, "file_format"))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    # "AUTHORITY:CODE" when the CRS is identifiable, otherwise its WKT; NULL when unknown.
    original_crs: Mapped[str | None] = mapped_column(Text)
    feature_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[FileStatus] = mapped_column(enum_column_type(FileStatus, "file_status"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
