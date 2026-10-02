"""Alembic environment for LitLattice.

When invoked from ``llat init`` a connection is passed in via
``config.attributes``. When invoked from the ``alembic`` CLI the database is
resolved like the application does (``LITLATTICE_DB`` or the XDG default).
"""

from logging.config import fileConfig

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy.engine import URL

from litlattice.database import create_engine
from litlattice.models import Base, UTCDateTime
from litlattice.paths import resolve_db_path

config = context.config

if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context: AutogenContext):
    # Migrations describe storage only; keep them independent of app types.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_as_batch=True,
        render_item=render_item,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=URL.create("sqlite", database=str(resolve_db_path())),
        literal_binds=True,
        dialect_name="sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(resolve_db_path())
    try:
        with engine.connect() as connection:
            _configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
