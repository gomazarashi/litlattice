from pathlib import Path
from shutil import copyfile

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from litlattice.database import (
    alembic_config,
    create_engine,
    open_database,
    upgrade_database,
)
from litlattice.errors import DatabaseMigrationFailed, DatabaseNotInitialized
from litlattice.installation import initialize
from litlattice.models import Base
from litlattice.papers import add_to_library, create_paper, list_papers


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
        paper = create_paper(engine, [("doi", "10.1000/example")], title="Sample")
        add_to_library(engine, paper.id)
        before = list_papers(engine)
        upgrade_database(engine)
        assert _current(engine) == _head()
        assert list_papers(engine) == before
    finally:
        engine.dispose()


def test_migrations_match_orm_metadata(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "render_as_batch": True}
        )
        assert compare_metadata(context, Base.metadata) == []


def test_unmigrated_database_requires_explicit_init(tmp_path: Path) -> None:
    db_path = tmp_path / "unmigrated.db"
    engine = create_engine(db_path)
    try:
        with engine.connect():
            pass
        with (
            pytest.raises(DatabaseNotInitialized, match="create or upgrade"),
            open_database(db_path),
        ):
            pytest.fail("An unmigrated database must not be opened")
        assert _current(engine) is None
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()

    initialize(db_path)
    with open_database(db_path) as engine:
        assert _current(engine) == _head()


def test_initialize_reports_sqlalchemy_migration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = OperationalError("CREATE TABLE paper", {}, RuntimeError("disk full"))
    db_path = tmp_path / "failure.db"

    def fail_upgrade(engine: Engine) -> None:
        raise error

    monkeypatch.setattr("litlattice.installation.upgrade_database", fail_upgrade)
    with pytest.raises(DatabaseMigrationFailed) as caught:
        initialize(db_path)
    assert caught.value.__cause__ is error
    assert caught.value.db_path == db_path
    assert str(error) in str(caught.value)
    assert "recreate" not in str(caught.value)
    assert "complete rollback is not guaranteed" in str(caught.value)


def test_stopped_database_copy_preserves_records(tmp_path: Path) -> None:
    db_path = tmp_path / "original.db"
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    initialize(db_path)
    with open_database(db_path) as engine:
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
                == "delete"
            )
        paper = create_paper(engine, [("doi", "10.1000/example")], title="Sample")
        add_to_library(engine, paper.id)
        before = list_papers(engine)
    assert not Path(f"{db_path}-journal").exists()
    assert not Path(f"{db_path}-wal").exists()
    copyfile(db_path, backup)
    with open_database(db_path) as engine:
        create_paper(engine, title="After backup")
    copyfile(backup, restored)
    with open_database(restored) as engine:
        assert list_papers(engine) == before
