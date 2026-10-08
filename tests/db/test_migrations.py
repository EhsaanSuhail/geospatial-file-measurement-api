import uuid

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import make_url

import app.models  # noqa: F401  (registers tables on Base.metadata)
from app.db.base import Base
from tests.conftest import alembic_config, create_database, drop_database


def test_migrated_schema_matches_orm_models(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_migrations_downgrade_and_upgrade_cleanly(test_database_url: str) -> None:
    # A throwaway database, so the shared test schema is never torn down mid-session.
    url = make_url(test_database_url).set(database=f"geomeasure_migrations_{uuid.uuid4().hex[:8]}")
    create_database(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            command.upgrade(alembic_config(connection), "head")
            command.downgrade(alembic_config(connection), "base")
            assert inspect(connection).get_table_names() == ["alembic_version"]
            command.upgrade(alembic_config(connection), "head")
            assert set(inspect(connection).get_table_names()) == {
                "alembic_version",
                "files",
                "features",
                "measurements",
            }
    finally:
        engine.dispose()
        drop_database(url)
