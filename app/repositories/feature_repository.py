import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models import Feature


class FeatureRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_all(self, rows: Sequence[dict[str, Any]]) -> None:
        """Bulk insert (executemany), much faster than adding ORM objects one by one."""
        if rows:
            self._session.execute(insert(Feature), rows)

    def list_for_file(self, file_id: uuid.UUID, limit: int, offset: int) -> list[Feature]:
        statement = (
            select(Feature)
            .where(Feature.file_id == file_id)
            .order_by(Feature.feature_index)
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(statement))
