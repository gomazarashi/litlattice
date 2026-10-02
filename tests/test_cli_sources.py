import shutil
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner, Result

from litlattice.cli import app
from litlattice.database import open_database
from litlattice.models import DocumentCopy
from litlattice.paths import normalize_local_path

runner = CliRunner()


def _invoke(db_path: Path, *args: str) -> Result:
    return runner.invoke(app, ["--db", str(db_path), *args])


def _source_id(stdout: str) -> str:
    return stdout.splitlines()[0].split()[2]


def _add_source(db_path: Path, path: Path) -> str:
    result = _invoke(db_path, "src", "add", str(path))
    assert result.exit_code == 0, result.output
    return _source_id(result.stdout)


def _document_copy_count(db_path: Path) -> int:
    with open_database(db_path) as engine, Session(engine) as session:
        return session.scalar(select(func.count()).select_from(DocumentCopy))


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "litlattice.db"
    result = _invoke(db_path, "init")
    assert result.exit_code == 0, result.output
    return db_path


@pytest.fixture
def pdf_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "papers"
    (directory / "sub").mkdir(parents=True)
    (directory / "a.pdf").write_bytes(b"%PDF-1.4 alpha")
    (directory / "sub" / "b.PDF").write_bytes(b"%PDF-1.4 beta")
    (directory / "note.txt").write_text("not a pdf")
    return directory


def test_src_add_registers_source_and_is_idempotent(db: Path, pdf_dir: Path) -> None:
    normalized = str(normalize_local_path(pdf_dir))

    first = _invoke(db, "src", "add", str(pdf_dir))

    assert first.exit_code == 0, first.output
    first_id = _source_id(first.stdout)
    assert first.stdout == f"Added source: {first_id}  {normalized}\n"

    second = _invoke(db, "src", "add", str(pdf_dir))

    assert second.exit_code == 0, second.output
    assert second.stdout == f"Source already registered: {first_id}  {normalized}\n"

    listing = _invoke(db, "src", "list")

    assert listing.exit_code == 0, listing.output
    assert listing.stdout.splitlines() == [
        f"{first_id}  {normalized}  last scanned: never"
    ]


def test_src_add_rejects_missing_directory(db: Path, tmp_path: Path) -> None:
    result = _invoke(db, "src", "add", str(tmp_path / "missing"))

    assert result.exit_code == 1
    assert "Error: Source must be an existing directory" in result.stderr
    assert _invoke(db, "src", "list").stdout == ""


def test_src_list_is_empty_without_sources(db: Path) -> None:
    result = _invoke(db, "src", "list")

    assert result.exit_code == 0
    assert result.stdout == ""


def test_src_list_shows_last_scanned(db: Path, pdf_dir: Path) -> None:
    source_id = _add_source(db, pdf_dir)
    normalized = str(normalize_local_path(pdf_dir))

    assert _invoke(db, "src", "list").stdout.splitlines() == [
        f"{source_id}  {normalized}  last scanned: never"
    ]

    assert _invoke(db, "scan").exit_code == 0

    [line] = _invoke(db, "src", "list").stdout.splitlines()
    prefix = f"{source_id}  {normalized}  last scanned: "
    assert line.startswith(prefix)
    assert datetime.fromisoformat(line.removeprefix(prefix)).tzinfo is not None


def test_src_remove_by_id_and_by_path(db: Path, pdf_dir: Path) -> None:
    source_id = _add_source(db, pdf_dir)
    normalized = str(normalize_local_path(pdf_dir))
    expected = (
        f"Removed source: {source_id}  {normalized}\n"
        "Document copies found under it are kept.\n"
    )

    by_id = _invoke(db, "src", "remove", source_id)

    assert by_id.exit_code == 0, by_id.output
    assert by_id.stdout == expected
    assert _invoke(db, "src", "list").stdout == ""

    re_added_id = _add_source(db, pdf_dir)
    by_path = _invoke(db, "src", "remove", str(pdf_dir))

    assert by_path.exit_code == 0, by_path.output
    assert by_path.stdout == (
        f"Removed source: {re_added_id}  {normalized}\n"
        "Document copies found under it are kept.\n"
    )
    assert _invoke(db, "src", "list").stdout == ""


def test_src_remove_unknown_source_fails(db: Path, tmp_path: Path) -> None:
    by_id = _invoke(db, "src", "remove", str(uuid.uuid4()))
    by_path = _invoke(db, "src", "remove", str(tmp_path / "unregistered"))

    assert by_id.exit_code == 1
    assert "Error: Source not found" in by_id.stderr
    assert by_path.exit_code == 1
    assert "Error: Source not found" in by_path.stderr


def test_removing_source_keeps_document_copies(db: Path, pdf_dir: Path) -> None:
    _add_source(db, pdf_dir)
    assert _invoke(db, "scan").exit_code == 0
    assert _document_copy_count(db) == 2

    result = _invoke(db, "src", "remove", str(pdf_dir))

    assert result.exit_code == 0, result.output
    assert _document_copy_count(db) == 2


def test_scan_is_idempotent(db: Path, pdf_dir: Path) -> None:
    _add_source(db, pdf_dir)
    normalized = str(normalize_local_path(pdf_dir))

    first = _invoke(db, "scan")

    assert first.exit_code == 0, first.output
    assert first.stdout.splitlines() == [
        f"{normalized}: 2 found, 2 new, 0 changed, 0 reappeared, 0 missing",
        f"  unidentified {normalized}/a.pdf",
        f"  unidentified {normalized}/sub/b.PDF",
        "Identified: 0 linked, 0 ambiguous, 2 unidentified.",
        "Scanned 1 source(s).",
    ]

    second = _invoke(db, "scan")

    assert second.exit_code == 0, second.output
    assert second.stdout.splitlines() == [
        f"{normalized}: 2 found, 0 new, 0 changed, 0 reappeared, 0 missing",
        "Scanned 1 source(s).",
    ]
    assert _document_copy_count(db) == 2


def test_scan_marks_deleted_files_missing(db: Path, pdf_dir: Path) -> None:
    _add_source(db, pdf_dir)
    assert _invoke(db, "scan").exit_code == 0
    normalized = str(normalize_local_path(pdf_dir))
    (pdf_dir / "a.pdf").unlink()

    result = _invoke(db, "scan")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"{normalized}: 1 found, 0 new, 0 changed, 0 reappeared, 1 missing",
        "Scanned 1 source(s).",
    ]
    assert _document_copy_count(db) == 2


def test_scan_reports_unavailable_source(db: Path, pdf_dir: Path) -> None:
    _add_source(db, pdf_dir)
    assert _invoke(db, "scan").exit_code == 0
    normalized = str(normalize_local_path(pdf_dir))
    shutil.rmtree(pdf_dir)

    result = _invoke(db, "scan")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"{normalized}: unavailable (directory not found; existing copies left as is)",
        "Scanned 1 source(s).",
    ]
    assert _document_copy_count(db) == 2


def test_scan_without_sources_hints_but_succeeds(db: Path) -> None:
    result = _invoke(db, "scan")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "No sources registered. Add one with `llat src add PATH`." in result.stderr


def test_scan_source_option_selects_sources(db: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.pdf").write_bytes(b"%PDF first")
    (second / "b.pdf").write_bytes(b"%PDF second")
    first_id = _add_source(db, first)
    second_id = _add_source(db, second)

    only_first = _invoke(db, "scan", "--source", first_id)

    assert only_first.exit_code == 0, only_first.output
    assert only_first.stdout.splitlines() == [
        (
            f"{normalize_local_path(first)}: 1 found, 1 new, 0 changed, "
            "0 reappeared, 0 missing"
        ),
        f"  unidentified {normalize_local_path(first)}/a.pdf",
        "Identified: 0 linked, 0 ambiguous, 1 unidentified.",
        "Scanned 1 source(s).",
    ]

    repeated = _invoke(db, "scan", "--source", first_id, "-s", second_id)

    assert repeated.exit_code == 0, repeated.output
    assert repeated.stdout.splitlines()[-1] == "Scanned 2 source(s)."
    assert _document_copy_count(db) == 2


def test_scan_unknown_source_option_fails(db: Path) -> None:
    result = _invoke(db, "scan", "--source", str(uuid.uuid4()))

    assert result.exit_code == 1
    assert "Error: Source not found" in result.stderr


def test_scan_does_not_add_to_library(db: Path, pdf_dir: Path) -> None:
    _add_source(db, pdf_dir)

    result = _invoke(db, "scan")

    assert result.exit_code == 0, result.output
    assert _invoke(db, "paper", "list").stdout == ""
    assert _document_copy_count(db) == 2


def test_src_list_and_scan_require_init(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    listing = _invoke(db_path, "src", "list")
    scanning = _invoke(db_path, "scan")

    assert listing.exit_code == 1
    assert "llat init" in listing.stderr
    assert scanning.exit_code == 1
    assert "llat init" in scanning.stderr
