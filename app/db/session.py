import json
from functools import partial

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        # Replaces connections the server has dropped (for example after a DB restart).
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        # JSON has no NaN/Infinity: fail loudly in Python rather than send invalid JSONB.
        json_serializer=partial(json.dumps, allow_nan=False, ensure_ascii=False),
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False lets services return ORM objects after committing without
    # triggering a lazy reload on attribute access.
    return sessionmaker(bind=engine, expire_on_commit=False)


def check_database_connection(session: Session) -> None:
    """Raise `sqlalchemy.exc.SQLAlchemyError` if the database cannot answer a trivial query."""
    session.execute(text("SELECT 1"))
