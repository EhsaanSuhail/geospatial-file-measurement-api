import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.enums import FileStatus
from app.models import FileRecord


class FileRepository:
    """Persistence for file records. Never commits: transaction boundaries belong to services."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: FileRecord) -> FileRecord:
        self._session.add(record)
        self._session.flush()
        return record

    def get(self, file_id: uuid.UUID) -> FileRecord | None:
        return self._session.get(FileRecord, file_id)

    def mark_failed(self, file_id: uuid.UUID, error_message: str) -> None:
        self._session.execute(
            update(FileRecord)
            .where(FileRecord.id == file_id)
            .values(status=FileStatus.FAILED, error_message=error_message)
        )
