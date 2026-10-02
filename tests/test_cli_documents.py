import uuid
from pathlib import Path

import pytest
from pdf_factory import write_pdf
from typer.testing import CliRunner, Result

from litlattice.cli import app
from litlattice.paths import normalize_local_path

runner = CliRunner()


def _invoke(db: Path, *args: str) -> Result:
    return runner.invoke(app, ["--db", str(db), *args])


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "litlattice.db"
    result = _invoke(db_path, "init")
    assert result.exit_code == 0, result.output
    return db_path


def _paper(db: Path, *options: str) -> str:
    result = _invoke(db, "paper", "create", *options)
    assert result.exit_code == 0, result.output
    return next(
        line.removeprefix("ID:").strip()
        for line in result.stdout.splitlines()
        if line.startswith("ID:")
    )


def _doc_lines(db: Path, *args: str) -> list[str]:
    result = _invoke(db, "doc", "list", *args)
    assert result.exit_code == 0, result.output
    return result.stdout.splitlines()


def _document_id(db: Path, name: str) -> str:
    return next(line.split()[0] for line in _doc_lines(db) if name in line)


def _scanned_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "papers"
    directory.mkdir()
    return directory


def test_scan_reports_created_paper(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(directory / "2401.12345v2.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0

    result = _invoke(db, "scan")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[-1] == "Scanned 1 source(s)."
    assert lines[-2] == "Identified: 1 linked, 0 ambiguous, 0 unidentified."
    prefix = f"  new paper   {normalize_local_path(pdf)} -> "
    assert lines[-3].startswith(prefix)
    paper_id = uuid.UUID(lines[-3].removeprefix(prefix))
    assert _invoke(db, "paper", "list").stdout == f"{paper_id}  -  arxiv:2401.12345\n"


def test_scan_reports_ambiguous_document_with_candidates(
    db: Path, tmp_path: Path
) -> None:
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(
        directory / "a.pdf", pages_text=["doi:10.5555/one and doi:10.5555/two"]
    )
    first = _paper(db, "--doi", "10.5555/one")
    second = _paper(db, "--doi", "10.5555/two")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0

    result = _invoke(db, "scan")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert (
        f"  ambiguous   {normalize_local_path(pdf)}  "
        "(doi:10.5555/one doi:10.5555/two)" in lines
    )
    assert {f"      candidate {first}", f"      candidate {second}"} <= set(lines)
    assert lines[-2] == "Identified: 0 linked, 1 ambiguous, 0 unidentified."
    assert lines[-1] == "Scanned 1 source(s)."


def test_scan_reports_unidentified_documents(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    normalized = normalize_local_path(directory)

    result = _invoke(db, "scan")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"{normalized}: 1 found, 1 new, 0 changed, 0 reappeared, 0 missing",
        f"  unidentified {normalized}/unknown.pdf",
        "Identified: 0 linked, 0 ambiguous, 1 unidentified.",
        "Scanned 1 source(s).",
    ]


def test_doc_list_shows_present_missing_and_links(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0
    document_id = _document_id(db, "unknown.pdf")
    paper = _paper(db, "--title", "Chosen")
    document_path = str(normalize_local_path(pdf))

    assert _doc_lines(db) == [f"{document_id}  present  {document_path}"]

    linked = _invoke(db, "doc", "link", document_id, paper)

    assert linked.exit_code == 0, linked.output
    assert linked.stdout == f"Linked: {document_id} -> {paper}\n"
    assert _doc_lines(db) == [f"{document_id}  present  {document_path}  -> {paper}"]

    pdf.unlink()
    assert _invoke(db, "scan").exit_code == 0

    assert _doc_lines(db) == [f"{document_id}  missing  {document_path}  -> {paper}"]


def test_doc_link_is_idempotent_and_unlink_keeps_everything(
    db: Path, tmp_path: Path
) -> None:
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0
    document_id = _document_id(db, "unknown.pdf")
    paper = _paper(db, "--title", "Chosen")

    assert _invoke(db, "doc", "link", document_id, paper).exit_code == 0
    again = _invoke(db, "doc", "link", document_id, paper)

    assert again.exit_code == 0, again.output
    assert again.stdout == f"Already linked: {document_id} -> {paper}\n"

    unlinked = _invoke(db, "doc", "unlink", document_id, paper)

    assert unlinked.exit_code == 0, unlinked.output
    assert unlinked.stdout == (
        f"Unlinked: {document_id} -> {paper}\n"
        "The document copy, the Paper and the file are kept.\n"
    )
    assert pdf.exists()
    assert f"ID:          {paper}\n" in _invoke(db, "show", paper).stdout
    assert _doc_lines(db) == [f"{document_id}  present  {normalize_local_path(pdf)}"]

    not_linked = _invoke(db, "doc", "unlink", document_id, paper)

    assert not_linked.exit_code == 0, not_linked.output
    assert not_linked.stdout == f"Not linked: {document_id} -> {paper}\n"


def test_doc_list_filters_by_paper_and_unlinked(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "one.pdf")
    write_pdf(directory / "two.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0
    first = _document_id(db, "one.pdf")
    second = _document_id(db, "two.pdf")
    paper = _paper(db, "--title", "Holder")
    assert _invoke(db, "doc", "link", first, paper).exit_code == 0

    unlinked = _doc_lines(db, "--unlinked")
    by_paper = _doc_lines(db, "--paper", paper)

    assert [line.split()[0] for line in unlinked] == [second]
    assert [line.split()[0] for line in by_paper] == [first]
    assert {line.split()[0] for line in _doc_lines(db)} == {first, second}


def test_doc_link_replaces_previous_paper(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0
    document_id = _document_id(db, "unknown.pdf")
    first = _paper(db, "--title", "One")
    second = _paper(db, "--title", "Two")
    assert _invoke(db, "doc", "link", document_id, first).exit_code == 0

    result = _invoke(db, "doc", "link", document_id, second)

    assert result.exit_code == 0, result.output
    assert result.stdout == f"Linked: {document_id} -> {second}\n"
    assert _doc_lines(db) == [
        f"{document_id}  present  {normalize_local_path(pdf)}  -> {second}"
    ]


def test_doc_unlink_only_clears_the_matching_paper(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0
    document_id = _document_id(db, "unknown.pdf")
    linked = _paper(db, "--title", "Linked")
    other = _paper(db, "--title", "Other")
    assert _invoke(db, "doc", "link", document_id, linked).exit_code == 0

    result = _invoke(db, "doc", "unlink", document_id, other)

    assert result.exit_code == 0, result.output
    assert result.stdout == f"Not linked: {document_id} -> {other}\n"
    assert _doc_lines(db) == [
        f"{document_id}  present  {normalize_local_path(pdf)}  -> {linked}"
    ]


def test_doc_identify_links_when_paper_appears_later(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(
        directory / "a.pdf", pages_text=["doi:10.5555/one and doi:10.5555/two"]
    )
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    scanned = _invoke(db, "scan")
    assert "  ambiguous   " in scanned.stdout
    document_id = _document_id(db, "a.pdf")
    paper = _paper(db, "--doi", "10.5555/one", "--doi", "10.5555/two")

    result = _invoke(db, "doc", "identify", document_id)

    assert result.exit_code == 0, result.output
    assert result.stdout == f"  linked      {normalize_local_path(pdf)} -> {paper}\n"
    assert not _doc_lines(db, "--unlinked")


def test_doc_identify_reports_unidentified_and_empty(db: Path, tmp_path: Path) -> None:
    nothing = _invoke(db, "doc", "identify")

    assert nothing.exit_code == 0, nothing.output
    assert nothing.stdout == "Nothing to identify.\n"

    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0

    result = _invoke(db, "doc", "identify")

    assert result.exit_code == 0, result.output
    assert result.stdout == f"  unidentified {normalize_local_path(pdf)}\n"


def test_doc_identify_rejects_unknown_document(db: Path) -> None:
    result = _invoke(db, "doc", "identify", str(uuid.uuid4()))

    assert result.exit_code == 1
    assert "Error: Document copy not found" in result.stderr


def test_doc_link_rejects_unknown_paper(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0
    document_id = _document_id(db, "unknown.pdf")

    result = _invoke(db, "doc", "link", document_id, str(uuid.uuid4()))

    assert result.exit_code == 1
    assert "Error: Paper not found" in result.stderr
