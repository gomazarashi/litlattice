"""SQLite engine setup and schema migration."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.engine import URL, Engine

from litlattice.errors import DatabaseNotInitialized
from litlattice.paths import resolve_db_path

MIGRATIONS_LOCATION = "litlattice:migrations"


def _enable_foreign_keys(dbapi_connection, connection_record) -> None:
    autocommit = dbapi_connection.autocommit
    dbapi_connection.autocommit = True

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

    dbapi_connection.autocommit = autocommit


def create_engine(db_path: Path) -> Engine:
    """Create an engine for the SQLite file at ``db_path``.

    Foreign key enforcement is enabled on every connection.
    """
    engine = sqlalchemy.create_engine(URL.create("sqlite", database=str(db_path)))
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_LOCATION)
    return config


def upgrade_database(engine: Engine) -> None:
    """Apply pending migrations; sqlite3 legacy mode may leave DDL on failure."""
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@contextmanager
def open_database(db_path: str | os.PathLike[str] | None = None) -> Iterator[Engine]:
    """Open an existing database whose schema is at the migration head.

    Never creates or migrates the database; that is ``llat init``'s job.
    """
    resolved = resolve_db_path(db_path)
    if not resolved.is_file():
        raise DatabaseNotInitialized(resolved, "Database does not exist")
    engine = create_engine(resolved)
    try:
        head = ScriptDirectory.from_config(alembic_config()).get_current_head()
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
        if current != head:
            raise DatabaseNotInitialized(
                resolved, f"Database schema is at {current}, expected {head}"
            )
        yield engine
    finally:
        engine.dispose()
