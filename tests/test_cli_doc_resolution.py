import json
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from pdf_factory import write_pdf
from typer.testing import CliRunner, Result

from litlattice.cli import app
from litlattice.paths import normalize_local_path
from litlattice.providers import ProviderWork

runner = CliRunner()

UNCONFIRMED_NOTE = (
    "OpenAlex's record shares no identifier with the given ones, so it cannot be "
    "confirmed to be the same work; nothing was stored."
)


def work(
    openalex: str,
    doi: str | None = None,
    title: str | None = None,
    year: int | None = None,
) -> ProviderWork:
    identifiers = [("openalex", openalex)]
    if doi:
        identifiers.append(("doi", doi))
    return ProviderWork(tuple(identifiers), title or openalex, year)


class FakeProvider:
    def __init__(self, works: Sequence[ProviderWork]) -> None:
        self.works = list(works)

    def lookup_work(self, identifiers):
        wanted = {(scheme, value.lower()) for scheme, value in identifiers}
        for candidate in self.works:
            keys = {(scheme, value.lower()) for scheme, value in candidate.identifiers}
            if keys & wanted:
                return candidate
        return None


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "litlattice.db"
    result = _invoke(db_path, "init")
    assert result.exit_code == 0, result.output
    return db_path


def _invoke(db: Path, *args: str) -> Result:
    return runner.invoke(app, ["--db", str(db), *args])


def _json(result: Result) -> dict:
    return json.loads(result.stdout)


def _paper(db: Path, *options: str) -> str:
    result = _invoke(db, "paper", "create", *options)
    assert result.exit_code == 0, result.output
    return next(
        line.removeprefix("ID:").strip()
        for line in result.stdout.splitlines()
        if line.startswith("ID:")
    )


def _use_provider(
    monkeypatch: pytest.MonkeyPatch, works: Sequence[ProviderWork]
) -> FakeProvider:
    fake = FakeProvider(works)
    monkeypatch.setattr("litlattice.cli._provider", lambda: fake)
    return fake


def _scanned_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "papers"
    directory.mkdir()
    return directory


def _scan(db: Path, directory: Path) -> None:
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0


def _doc_lines(db: Path) -> list[str]:
    result = _invoke(db, "doc", "list")
    assert result.exit_code == 0, result.output
    return result.stdout.splitlines()


def _document_id(db: Path, name: str) -> str:
    return next(line.split()[0] for line in _doc_lines(db) if name in line)


def test_doc_show_reports_ambiguous_strong_hints(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(directory / "2401.12345v2.pdf", metadata={"/doi": "10.5555/one"})
    _scan(db, directory)
    document_id = _document_id(db, "2401.12345v2.pdf")

    result = _invoke(db, "doc", "show", document_id)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert f"ID:         {document_id}" in lines
    assert f"Path:       {normalize_local_path(pdf)}" in lines
    assert "State:      present" in lines
    assert "Linked to:  (none)" in lines
    assert "Hints:" in lines
    assert f"  {'filename':<12} strong arxiv:2401.12345  -> (no Paper)" in lines
    assert f"  {'pdf_metadata':<12} strong doi:10.5555/one  -> (no Paper)" in lines
    assert "Assessment: ambiguous" in lines
    assert "Candidates:" not in lines
    start = lines.index("Next steps:")
    assert lines[start:] == [
        "Next steps:",
        f"  llat doc import {document_id} --arxiv 2401.12345",
        f"  llat doc import {document_id} --doi 10.5555/one",
    ]


def test_doc_show_hint_points_to_existing_paper(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "2401.12345v2.pdf", metadata={"/doi": "10.5555/one"})
    _scan(db, directory)
    document_id = _document_id(db, "2401.12345v2.pdf")
    paper = _paper(db, "--doi", "10.5555/one")

    result = _invoke(db, "doc", "show", document_id)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert f"  {'filename':<12} strong arxiv:2401.12345  -> (no Paper)" in lines
    assert f"  {'pdf_metadata':<12} strong doi:10.5555/one  -> {paper}" in lines
    assert "Assessment: ambiguous" in lines
    assert "Candidates:" in lines
    assert f"  {paper}  doi:10.5555/one" in lines
    start = lines.index("Next steps:")
    assert lines[start:] == [
        "Next steps:",
        f"  llat doc link {document_id} {paper}",
        f"  llat doc import {document_id} --arxiv 2401.12345",
    ]


def test_doc_show_without_identifiers_suggests_search(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "unknown.pdf")
    _scan(db, directory)
    document_id = _document_id(db, "unknown.pdf")

    result = _invoke(db, "doc", "show", document_id)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "Hints:" in lines
    assert "  (none found)" in lines
    assert "Assessment: unidentified" in lines
    start = lines.index("Next steps:")
    assert lines[start:] == [
        "Next steps:",
        '  llat search "<title>"',
        f"  llat doc import {document_id} --openalex <ID>",
    ]


def test_doc_show_linked_document_omits_next_steps(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "unknown.pdf")
    _scan(db, directory)
    document_id = _document_id(db, "unknown.pdf")
    paper = _paper(db, "--title", "Chosen")
    assert _invoke(db, "doc", "link", document_id, paper).exit_code == 0

    result = _invoke(db, "doc", "show", document_id)

    assert result.exit_code == 0, result.output
    assert f"Linked to:  {paper}  Chosen" in result.stdout.splitlines()
    assert "Assessment:" not in result.stdout
    assert "Next steps:" not in result.stdout


def test_doc_show_json(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "2401.12345v2.pdf", metadata={"/doi": "10.5555/one"})
    _scan(db, directory)
    document_id = _document_id(db, "2401.12345v2.pdf")

    result = _invoke(db, "doc", "show", document_id, "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    payload = _json(result)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["document"]["id"] == document_id
    assert data["document"]["paper_id"] is None
    assert data["file_available"] is True
    assert [hint["scheme"] for hint in data["hints"]] == ["arxiv", "doi"]
    assert [hint["strong"] for hint in data["hints"]] == [True, True]
    assert data["assessment"] == "ambiguous"
    assert data["candidates"] == []


def test_doc_show_unknown_document(db: Path) -> None:
    unknown = uuid.uuid4()

    result = _invoke(db, "doc", "show", str(unknown))

    assert result.exit_code == 1
    assert f"Error: Document copy not found: {unknown}" in result.stderr

    json_result = _invoke(db, "doc", "show", str(unknown), "--json")

    assert json_result.exit_code == 1
    assert json_result.stdout.count("\n") == 1
    payload = _json(json_result)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "DocumentNotFound"


def test_doc_import_creates_paper_and_links(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [work("W1", "10.1/s", title="Seed", year=2020)])
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "unknown.pdf")
    _scan(db, directory)
    document_id = _document_id(db, "unknown.pdf")

    result = _invoke(db, "doc", "import", document_id, "--doi", "10.1/s")

    assert result.exit_code == 0, result.output
    assert result.stderr == "Fetching from OpenAlex...\n"
    lines = result.stdout.splitlines()
    assert lines[0] == "Imported a new Paper."
    paper_id = next(
        line.removeprefix("ID:").strip() for line in lines if line.startswith("ID:")
    )
    linked_line = f"Linked: {document_id} -> {paper_id}"
    note_line = f"Not added to the Library. Add it with `llat lib add {paper_id}`."
    assert linked_line in lines
    assert note_line in lines
    assert lines.index(linked_line) < lines.index(note_line)
    document_path = normalize_local_path(directory / "unknown.pdf")
    assert _doc_lines(db) == [f"{document_id}  present  {document_path}  -> {paper_id}"]
    (paper_line,) = _invoke(db, "paper", "list").stdout.splitlines()
    assert paper_line.startswith(f"{paper_id}  -  ")


def test_doc_import_again_reports_already_linked(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [work("W1", "10.1/s", title="Seed", year=2020)])
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "unknown.pdf")
    _scan(db, directory)
    document_id = _document_id(db, "unknown.pdf")
    first = _invoke(db, "doc", "import", document_id, "--doi", "10.1/s")
    assert first.exit_code == 0, first.output
    paper_id = next(
        line.removeprefix("ID:").strip()
        for line in first.stdout.splitlines()
        if line.startswith("ID:")
    )

    result = _invoke(db, "doc", "import", document_id, "--doi", "10.1/s")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "Matched an existing Paper."
    assert "Added identifiers" not in result.stdout
    assert f"Already linked: {document_id} -> {paper_id}" in lines


def test_doc_import_json(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [work("W1", "10.1/s", title="Seed", year=2020)])
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "unknown.pdf")
    _scan(db, directory)
    document_id = _document_id(db, "unknown.pdf")

    result = _invoke(db, "doc", "import", document_id, "--doi", "10.1/s", "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    data = _json(result)["data"]
    assert data["document_id"] == document_id
    assert data["linked"] is True
    assert data["import_result"]["status"] == "created"
    assert data["import_result"]["paper_id"] is not None
    assert data["import_result"]["title"] == "Seed"


def test_doc_import_unconfirmed_does_not_link(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = ProviderWork(
        (("openalex", "W1"), ("doi", "10.1/published")), "Published", 2021
    )

    class AlwaysPublished(FakeProvider):
        def lookup_work(self, identifiers):
            return published

    monkeypatch.setattr(
        "litlattice.cli._provider", lambda: AlwaysPublished([published])
    )
    directory = _scanned_dir(tmp_path)
    pdf = write_pdf(directory / "unknown.pdf")
    _scan(db, directory)
    document_id = _document_id(db, "unknown.pdf")

    result = _invoke(db, "doc", "import", document_id, "--arxiv", "2401.12345")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "Fetching from OpenAlex...",
        UNCONFIRMED_NOTE,
    ]
    assert _doc_lines(db) == [f"{document_id}  present  {normalize_local_path(pdf)}"]
    assert _invoke(db, "paper", "list").stdout == ""


def test_doc_import_requires_an_identifier(db: Path, tmp_path: Path) -> None:
    directory = _scanned_dir(tmp_path)
    write_pdf(directory / "unknown.pdf")
    _scan(db, directory)
    document_id = _document_id(db, "unknown.pdf")

    result = _invoke(db, "doc", "import", document_id)

    assert result.exit_code == 2
