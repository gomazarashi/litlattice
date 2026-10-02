import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from litlattice.cli import app
from litlattice.database import open_database
from litlattice.papers import PaperRecord, list_papers

runner = CliRunner()


def _invoke(db_path: Path, *args: str) -> Result:
    return runner.invoke(app, ["--db", str(db_path), *args])


def _init(db_path: Path) -> None:
    result = _invoke(db_path, "init")
    assert result.exit_code == 0, result.output


@pytest.fixture
def initialized_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "litlattice.db"
    _init(db_path)
    return db_path


def _create_paper(db_path: Path, *options: str) -> tuple[uuid.UUID, str]:
    result = _invoke(db_path, "paper", "create", *options)
    assert result.exit_code == 0, result.output
    id_line = next(
        line for line in result.stdout.splitlines() if line.startswith("ID:")
    )
    return uuid.UUID(id_line.removeprefix("ID:").strip()), result.stdout


def _core_papers(db_path: Path) -> list[PaperRecord]:
    with open_database(db_path) as engine:
        return list_papers(engine)


def test_paper_create_normalizes_identifiers(initialized_db: Path) -> None:
    paper_id, stdout = _create_paper(
        initialized_db,
        "--doi",
        "10.1000/ABC",
        "--arxiv",
        "arXiv:2401.12345v2",
        "--doi",
        "https://doi.org/10.2000/DEF",
    )

    assert stdout.startswith(f"ID:          {paper_id}\n")
    assert "In library:  no" in stdout
    assert "Identifiers:" in stdout
    assert "  arxiv:2401.12345" in stdout
    assert "  doi:10.1000/abc" in stdout
    assert "  doi:10.2000/def" in stdout

    [paper] = _core_papers(initialized_db)
    assert paper.id == paper_id
    assert paper.in_library is False
    assert [(i.scheme, i.normalized_value) for i in paper.identifiers] == [
        ("arxiv", "2401.12345"),
        ("doi", "10.1000/abc"),
        ("doi", "10.2000/def"),
    ]


def test_paper_create_without_identifiers(initialized_db: Path) -> None:
    paper_id, stdout = _create_paper(initialized_db)

    assert stdout.startswith(f"ID:          {paper_id}\n")
    assert "In library:  no" in stdout
    assert "Identifiers: (none)" in stdout


def test_paper_create_rejects_duplicate_identifier(initialized_db: Path) -> None:
    _create_paper(initialized_db, "--doi", "10.1000/abc")

    result = _invoke(initialized_db, "paper", "create", "--doi", "DOI:10.1000/ABC")

    assert result.exit_code == 1
    assert "Identifier already assigned" in result.stderr
    assert "doi:10.1000/abc" in result.stderr
    assert len(_core_papers(initialized_db)) == 1


def test_paper_create_rejects_invalid_doi(initialized_db: Path) -> None:
    result = _invoke(initialized_db, "paper", "create", "--doi", "not-a-doi")

    assert result.exit_code == 1
    assert "Invalid doi identifier" in result.stderr
    assert _core_papers(initialized_db) == []


def test_show_displays_paper(initialized_db: Path) -> None:
    paper_id, create_stdout = _create_paper(initialized_db, "--doi", "10.1000/abc")

    result = _invoke(initialized_db, "show", str(paper_id))

    assert result.exit_code == 0
    assert result.stdout == create_stdout


def test_show_unknown_paper_fails(initialized_db: Path) -> None:
    unknown = uuid.uuid4()

    result = _invoke(initialized_db, "show", str(unknown))

    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_paper_list_marks_library_membership_in_creation_order(
    initialized_db: Path,
) -> None:
    first, _ = _create_paper(initialized_db, "--doi", "10.1000/first")
    second, _ = _create_paper(initialized_db, "--doi", "10.1000/second")

    added = _invoke(initialized_db, "lib", "add", str(second))
    assert added.exit_code == 0, added.output

    listing = _invoke(initialized_db, "paper", "list")

    assert listing.exit_code == 0
    assert listing.stdout.splitlines() == [
        f"{first}  -  doi:10.1000/first",
        f"{second}  L  doi:10.1000/second",
    ]


def test_paper_list_is_empty_without_papers(initialized_db: Path) -> None:
    result = _invoke(initialized_db, "paper", "list")

    assert result.exit_code == 0
    assert result.stdout == ""


def test_library_add_and_remove_are_idempotent(initialized_db: Path) -> None:
    paper_id, _ = _create_paper(initialized_db, "--doi", "10.1000/abc")

    first_add = _invoke(initialized_db, "lib", "add", str(paper_id))
    second_add = _invoke(initialized_db, "lib", "add", str(paper_id))
    first_remove = _invoke(initialized_db, "lib", "remove", str(paper_id))
    second_remove = _invoke(initialized_db, "lib", "remove", str(paper_id))

    assert first_add.exit_code == 0
    assert first_add.stdout == f"Added to library: {paper_id}\n"
    assert second_add.exit_code == 0
    assert second_add.stdout == f"Already in library: {paper_id}\n"
    assert first_remove.exit_code == 0
    assert first_remove.stdout == f"Removed from library: {paper_id}\n"
    assert second_remove.exit_code == 0
    assert second_remove.stdout == f"Not in library: {paper_id}\n"

    listing = _invoke(initialized_db, "paper", "list")
    assert listing.stdout == f"{paper_id}  -  doi:10.1000/abc\n"


def test_library_add_unknown_paper_fails(initialized_db: Path) -> None:
    result = _invoke(initialized_db, "lib", "add", str(uuid.uuid4()))

    assert result.exit_code == 1
    assert "not found" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("show", "not-a-uuid"),
        ("lib", "add", "123"),
        ("lib", "remove", "not-a-uuid"),
    ],
)
def test_malformed_uuid_is_a_usage_error(
    initialized_db: Path, args: tuple[str, ...]
) -> None:
    result = _invoke(initialized_db, *args)

    assert result.exit_code == 2
    assert "Invalid value" in result.stderr


def test_missing_database_is_reported_and_not_created(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    result = _invoke(db_path, "paper", "list")

    assert result.exit_code == 1
    assert "llat init" in result.stderr
    assert not db_path.exists()


def test_litlattice_db_env_var_selects_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "env.db"
    monkeypatch.setenv("LITLATTICE_DB", str(db_path))

    init_result = runner.invoke(app, ["init"])
    create_result = runner.invoke(app, ["paper", "create", "--doi", "10.1000/env"])
    listing = runner.invoke(app, ["paper", "list"])

    assert init_result.exit_code == 0, init_result.output
    assert create_result.exit_code == 0, create_result.output
    assert db_path.is_file()
    assert "doi:10.1000/env" in listing.stdout
    assert len(_core_papers(db_path)) == 1


def test_db_option_takes_precedence_over_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_db = tmp_path / "env.db"
    explicit_db = tmp_path / "explicit.db"
    monkeypatch.setenv("LITLATTICE_DB", str(env_db))

    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["--db", str(explicit_db), "init"]).exit_code == 0
    create_result = runner.invoke(
        app, ["--db", str(explicit_db), "paper", "create", "--doi", "10.1000/x"]
    )
    assert create_result.exit_code == 0, create_result.output

    explicit_listing = runner.invoke(app, ["--db", str(explicit_db), "paper", "list"])
    env_listing = runner.invoke(app, ["paper", "list"])

    assert "doi:10.1000/x" in explicit_listing.stdout
    assert "doi:10.1000/x" not in env_listing.stdout
