from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from litlattice.database import alembic_config, create_engine, upgrade_database
from litlattice.models import Base


def _head() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def _current(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_single_initial_migration() -> None:
    script = ScriptDirectory.from_config(alembic_config())

    assert script.get_heads() == ["0001"]
    revisions = list(script.walk_revisions())
    assert [revision.revision for revision in revisions] == ["0001"]
    assert revisions[0].down_revision is None


def test_upgrade_head_creates_schema(engine: Engine) -> None:
    assert _current(engine) == _head()
    assert set(inspect(engine).get_table_names()) == {
        "alembic_version",
        "paper",
        "paper_identifier",
        "source",
        "document_copy",
        "citation",
    }


def test_upgrade_is_repeatable(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "repeat.db")
    try:
        upgrade_database(engine)
        upgrade_database(engine)
        assert _current(engine) == _head()
    finally:
        engine.dispose()


def test_migrations_match_orm_metadata(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "render_as_batch": True}
        )
        assert compare_metadata(context, Base.metadata) == []
