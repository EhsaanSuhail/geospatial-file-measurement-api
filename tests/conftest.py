import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://geomeasure:geomeasure@127.0.0.1:5432/geomeasure_test"
)
# Never connected to: for tests that exercise the HTTP layer without a database.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/unreachable"


def _resolve_test_database_url() -> URL:
    """TEST_DATABASE_URL, else DATABASE_URL with a `_test` suffix (never the dev database)."""
    if explicit := os.environ.get("TEST_DATABASE_URL"):
        return make_url(explicit)
    try:
        base = make_url(Settings().database_url)
    except ValidationError:
        return make_url(_DEFAULT_TEST_DATABASE_URL)
    return base.set(database=f"{base.database}_test")


def create_database(url: URL) -> None:
    with _admin_connection(url) as connection:
        exists = connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{url.database}"'))


def drop_database(url: URL) -> None:
    with _admin_connection(url) as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))


@contextmanager
def _admin_connection(url: URL) -> Iterator[Connection]:
    """Connection to the server's maintenance database, for CREATE/DROP DATABASE."""
    engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def alembic_config(connection: Connection) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = _resolve_test_database_url()
    create_database(url)
    rendered = url.render_as_string(hide_password=False)
    engine = create_engine(rendered)
    try:
        with engine.begin() as connection:
            command.upgrade(alembic_config(connection), "head")
    finally:
        engine.dispose()
    return rendered


@pytest.fixture(scope="session")
def db_engine(test_database_url: str) -> Iterator[Engine]:
    engine = create_engine(test_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_database(db_engine: Engine) -> Iterator[None]:
    yield
    with db_engine.begin() as connection:
        connection.execute(text("TRUNCATE files CASCADE"))


@pytest.fixture
def db_session(db_engine: Engine, clean_database: None) -> Iterator[Session]:
    with Session(db_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def settings(test_database_url: str) -> Settings:
    return Settings(database_url=test_database_url)


@pytest.fixture
def client(settings: Settings, clean_database: None) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def offline_client() -> Iterator[TestClient]:
    """Client whose database is unreachable; for HTTP-layer behaviour only."""
    settings = Settings(database_url=UNREACHABLE_DATABASE_URL, max_upload_size_mb=1)
    with TestClient(create_app(settings), raise_server_exceptions=False) as test_client:
        yield test_client
