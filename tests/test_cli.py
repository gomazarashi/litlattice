import subprocess
import sys
import sysconfig
from importlib.metadata import version
from pathlib import Path

import click
import pytest
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from typer.testing import CliRunner

from litlattice.cli import app
from litlattice.database import alembic_config, create_engine

runner = CliRunner()
scripts_dir = Path(sysconfig.get_path("scripts"))


def test_help_succeeds() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    # Typer colors help output on CI (e.g. GITHUB_ACTIONS); compare plain text.
    assert "--version" in click.unstyle(result.output)


def test_version_matches_project_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"litlattice {version('litlattice')}"


@pytest.mark.parametrize(
    "command",
    [
        [str(scripts_dir / "llat")],
        [str(scripts_dir / "litlattice")],
        [sys.executable, "-m", "litlattice"],
    ],
    ids=["llat", "litlattice", "python-m"],
)
def test_entry_points_report_version(command: list[str]) -> None:
    result = subprocess.run(
        [*command, "--version"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0
    assert result.stdout.strip() == f"litlattice {version('litlattice')}"


def test_init_creates_database_and_is_idempotent(
    isolated_environment: Path,
) -> None:
    home = isolated_environment
    db_path = home / ".local/share/litlattice/litlattice.db"

    first = runner.invoke(app, ["init"])
    second = runner.invoke(app, ["init"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert db_path.is_file()
    assert str(db_path) in second.output

    engine = create_engine(db_path)
    try:
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "citation",
            "document_copy",
            "paper",
            "paper_identifier",
            "source",
        }
        with engine.connect() as connection:
            for table in (
                "paper",
                "paper_identifier",
                "source",
                "document_copy",
                "citation",
            ):
                count = connection.execute(
                    text(f"SELECT COUNT(*) FROM {table}")
                ).scalar_one()
                assert count == 0, f"expected no rows in {table}"

            head = ScriptDirectory.from_config(alembic_config()).get_current_head()
            current = MigrationContext.configure(connection).get_current_revision()
        assert current == head
    finally:
        engine.dispose()


def test_init_with_db_option(tmp_path: Path) -> None:
    db_path = tmp_path / "custom/llat.db"

    result = runner.invoke(app, ["--db", str(db_path), "init"])

    assert result.exit_code == 0, result.output
    assert db_path.is_file()


def test_init_with_env_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "env.db"
    monkeypatch.setenv("LITLATTICE_DB", str(db_path))

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert db_path.is_file()
