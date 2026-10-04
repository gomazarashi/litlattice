import json
import uuid
from pathlib import Path

import pytest
from pdf_factory import write_pdf
from typer.testing import CliRunner, Result

from litlattice.cli import _CONFLICT_NOTE, _UNCONFIRMED_NOTE, app
from litlattice.paths import normalize_local_path
from litlattice.providers import ProviderWork

runner = CliRunner()


class FakePaperProvider:
    def __init__(self, work: ProviderWork | None) -> None:
        self.work = work

    def lookup_work(self, identifiers):
        return self.work


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "litlattice.db"
    result = runner.invoke(app, ["--db", str(db_path), "init"])
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


def _use_provider(monkeypatch: pytest.MonkeyPatch, work: ProviderWork | None) -> None:
    monkeypatch.setattr("litlattice.cli._provider", lambda: FakePaperProvider(work))


def _document_id(db: Path, name: str) -> str:
    result = _invoke(db, "doc", "list", "--json")
    return next(
        document["id"] for document in _json(result)["data"] if name in document["path"]
    )


def test_init_json_and_short_alias(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"

    result = runner.invoke(app, ["--db", str(db_path), "init", "--json"])

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    assert db_path.is_file()
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["warnings"] == []
    assert payload["data"] == {"db_path": str(db_path)}

    short = runner.invoke(app, ["--db", str(db_path), "init", "-j"])

    assert short.exit_code == 0, short.output
    assert _json(short)["data"] == {"db_path": str(db_path)}


def test_paper_create_json(db: Path) -> None:
    result = _invoke(
        db,
        "paper",
        "create",
        "--doi",
        "10.1000/ABC",
        "--title",
        "Seed",
        "--json",
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    data = _json(result)["data"]
    assert data["title"] == "Seed"
    assert data["in_library"] is False
    assert data["publication_year"] is None
    assert data["identifiers"] == [
        {"scheme": "doi", "value": "10.1000/ABC", "normalized_value": "10.1000/abc"}
    ]

    duplicate = _invoke(db, "paper", "create", "--doi", "10.1000/abc", "--json")

    assert duplicate.exit_code == 1
    assert duplicate.stdout.count("\n") == 1
    assert _json(duplicate)["ok"] is False
    assert _json(duplicate)["error"]["type"] == "IdentifierConflict"

    short = _invoke(db, "paper", "create", "--doi", "10.1000/def", "-j")

    assert short.exit_code == 0, short.output
    assert _json(short)["data"]["identifiers"][0]["normalized_value"] == "10.1000/def"


def test_lib_add_and_remove_json(db: Path) -> None:
    paper = _paper(db, "--title", "Holder")

    added = _invoke(db, "lib", "add", paper, "--json")

    assert added.exit_code == 0, added.output
    assert _json(added)["data"] == {"paper_id": paper, "added": True}

    again = _invoke(db, "lib", "add", paper, "-j")

    assert again.exit_code == 0, again.output
    assert _json(again)["data"] == {"paper_id": paper, "added": False}

    removed = _invoke(db, "lib", "remove", paper, "--json")

    assert removed.exit_code == 0, removed.output
    assert _json(removed)["data"] == {"paper_id": paper, "removed": True}

    not_there = _invoke(db, "lib", "remove", paper, "--json")

    assert not_there.exit_code == 0, not_there.output
    assert _json(not_there)["data"] == {"paper_id": paper, "removed": False}

    unknown = _invoke(db, "lib", "add", str(uuid.uuid4()), "--json")

    assert unknown.exit_code == 1
    assert _json(unknown)["error"]["type"] == "PaperNotFound"
    assert _json(_invoke(db, "show", paper, "--json"))["data"]["id"] == paper


def test_citation_add_and_list_json_direction(db: Path) -> None:
    a = _paper(db, "--title", "A")
    b = _paper(db, "--title", "B")

    created = _invoke(db, "citation", "add", a, b, "--json")

    assert created.exit_code == 0, created.output
    assert _json(created)["data"] == {
        "citing_paper_id": a,
        "cited_paper_id": b,
        "created": True,
    }

    again = _invoke(db, "citation", "add", a, b, "-j")

    assert again.exit_code == 0, again.output
    assert _json(again)["data"]["created"] is False

    references = _json(
        _invoke(db, "citation", "list", a, "--direction", "references", "--json")
    )["data"]
    assert references["paper_id"] == a
    assert references["direction"] == "references"
    assert [record["id"] for record in references["references"]] == [b]
    assert references["citations"] == []

    cited_by = _json(
        _invoke(db, "citation", "list", b, "--direction", "citations", "--json")
    )["data"]
    assert cited_by["references"] == []
    assert [record["id"] for record in cited_by["citations"]] == [a]

    both = _json(_invoke(db, "citation", "list", a, "--json"))["data"]
    assert both["direction"] == "both"
    assert [record["id"] for record in both["references"]] == [b]
    assert both["citations"] == []

    self_cite = _invoke(db, "citation", "add", a, a, "--json")
    assert self_cite.exit_code == 1
    assert _json(self_cite)["error"]["type"] == "SelfCitation"

    unknown = _invoke(db, "citation", "list", str(uuid.uuid4()), "--json")
    assert unknown.exit_code == 1
    assert _json(unknown)["error"]["type"] == "PaperNotFound"

    papers = {
        record["id"]: record["in_library"]
        for record in _json(_invoke(db, "paper", "list", "--json"))["data"]
    }
    assert papers == {a: False, b: False}


def test_path_json_follows_direction(db: Path) -> None:
    a = _paper(db, "--title", "A")
    b = _paper(db, "--title", "B")
    c = _paper(db, "--title", "C")
    assert _invoke(db, "citation", "add", a, b).exit_code == 0
    assert _invoke(db, "citation", "add", b, c).exit_code == 0

    data = _json(_invoke(db, "path", a, c, "--json"))["data"]

    assert data["source_paper_id"] == a
    assert data["target_paper_id"] == c
    assert data["hops"] == 2
    assert [record["id"] for record in data["papers"]] == [a, b, c]
    assert [record["title"] for record in data["papers"]] == ["A", "B", "C"]

    backward = _invoke(db, "path", c, a, "-j")

    assert backward.exit_code == 1
    assert backward.stdout.count("\n") == 1
    assert _json(backward)["error"]["type"] == "CitationPathNotFound"

    same = _json(_invoke(db, "path", a, a, "--json"))["data"]

    assert same["hops"] == 0
    assert [record["id"] for record in same["papers"]] == [a]


def test_src_add_remove_json_keeps_documents(db: Path, tmp_path: Path) -> None:
    directory = tmp_path / "papers"
    directory.mkdir()
    pdf = write_pdf(directory / "unknown.pdf")
    normalized = str(normalize_local_path(directory))

    added = _invoke(db, "src", "add", str(directory), "--json")

    assert added.exit_code == 0, added.output
    data = _json(added)["data"]
    assert data["created"] is True
    assert data["source"]["normalized_path"] == normalized
    assert data["source"]["last_scanned_at"] is None
    source_id = data["source"]["id"]

    again = _invoke(db, "src", "add", str(directory), "-j")

    assert again.exit_code == 0, again.output
    assert _json(again)["data"]["created"] is False
    assert _json(again)["data"]["source"]["id"] == source_id

    assert _invoke(db, "scan").exit_code == 0

    removed = _invoke(db, "src", "remove", source_id, "--json")

    assert removed.exit_code == 0, removed.output
    assert _json(removed)["data"]["id"] == source_id
    assert _json(removed)["data"]["normalized_path"] == normalized

    documents = _json(_invoke(db, "doc", "list", "--json"))["data"]
    assert [document["path"] for document in documents] == [
        str(normalize_local_path(pdf))
    ]

    missing = _invoke(db, "src", "remove", str(directory), "--json")

    assert missing.exit_code == 1
    assert _json(missing)["error"]["type"] == "SourceNotFound"


def test_doc_link_and_unlink_json(db: Path, tmp_path: Path) -> None:
    directory = tmp_path / "papers"
    directory.mkdir()
    pdf = write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0
    document = _document_id(db, "unknown.pdf")
    paper = _paper(db, "--title", "Holder")

    linked = _invoke(db, "doc", "link", document, paper, "--json")

    assert linked.exit_code == 0, linked.output
    assert _json(linked)["data"] == {
        "document_id": document,
        "paper_id": paper,
        "linked": True,
    }

    again = _invoke(db, "doc", "link", document, paper, "-j")

    assert again.exit_code == 0, again.output
    assert _json(again)["data"]["linked"] is False

    papers = _json(_invoke(db, "paper", "list", "--json"))["data"]
    assert [(record["id"], record["in_library"]) for record in papers] == [
        (paper, False)
    ]

    unlinked = _invoke(db, "doc", "unlink", document, paper, "--json")

    assert unlinked.exit_code == 0, unlinked.output
    assert _json(unlinked)["data"] == {
        "document_id": document,
        "paper_id": paper,
        "unlinked": True,
    }
    assert pdf.exists()

    again = _invoke(db, "doc", "unlink", document, paper, "--json")

    assert again.exit_code == 0, again.output
    assert _json(again)["data"]["unlinked"] is False

    unknown = _invoke(db, "doc", "link", str(uuid.uuid4()), paper, "--json")

    assert unknown.exit_code == 1
    assert _json(unknown)["error"]["type"] == "DocumentNotFound"


def test_doc_identify_json_empty_and_linked(db: Path, tmp_path: Path) -> None:
    nothing = _invoke(db, "doc", "identify", "--json")

    assert nothing.exit_code == 0, nothing.output
    assert nothing.stdout.count("\n") == 1
    assert _json(nothing)["data"] == []

    directory = tmp_path / "papers"
    directory.mkdir()
    pdf = write_pdf(
        directory / "a.pdf", pages_text=["doi:10.5555/one and doi:10.5555/two"]
    )
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0
    document = _document_id(db, "a.pdf")
    paper = _paper(db, "--doi", "10.5555/one", "--doi", "10.5555/two")

    identified = _invoke(db, "doc", "identify", document, "-j")

    assert identified.exit_code == 0, identified.output
    (record,) = _json(identified)["data"]
    assert record["document_id"] == document
    assert record["path"] == str(normalize_local_path(pdf))
    assert record["status"] == "linked"
    assert record["paper_id"] == paper
    assert record["candidate_paper_ids"] == [paper]

    assert _json(_invoke(db, "doc", "identify", "--json"))["data"] == []

    unknown = _invoke(db, "doc", "identify", str(uuid.uuid4()), "--json")

    assert unknown.exit_code == 1
    assert _json(unknown)["error"]["type"] == "DocumentNotFound"


def test_paper_fetch_json_updated(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(
        monkeypatch,
        ProviderWork((("openalex", "W1"), ("doi", "10.1/s")), "Seed", 2020),
    )
    paper = _paper(db, "--doi", "10.1/s")

    result = _invoke(db, "paper", "fetch", paper, "--json")

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    payload = _json(result)
    assert payload["warnings"] == []
    data = payload["data"]
    assert data["paper_id"] == paper
    assert data["status"] == "updated"
    assert data["title_set"] is True
    assert data["publication_year_set"] is True
    assert [identifier["scheme"] for identifier in data["identifiers_added"]] == [
        "openalex"
    ]
    assert _json(_invoke(db, "show", paper, "--json"))["data"]["title"] == "Seed"


def test_paper_fetch_json_unconfirmed_is_a_warning(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = ProviderWork(
        (("openalex", "W1"), ("doi", "10.1/published")), "Published", 2021
    )
    _use_provider(monkeypatch, published)
    paper = _paper(db, "--arxiv", "2401.12345")

    result = _invoke(db, "paper", "fetch", paper, "-j")

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = _json(result)
    assert payload["data"]["status"] == "unconfirmed"
    assert payload["warnings"] == [_UNCONFIRMED_NOTE]
    assert _json(_invoke(db, "show", paper, "--json"))["data"]["title"] is None


def test_paper_fetch_json_conflict_is_a_warning(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    twin = ProviderWork(
        (("openalex", "W9"), ("doi", "10.1/a"), ("doi", "10.2/b")), "Twin", 2020
    )
    _use_provider(monkeypatch, twin)
    paper = _paper(db, "--doi", "10.1/a")
    _paper(db, "--doi", "10.2/b")

    result = _invoke(db, "paper", "fetch", paper, "--json")

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = _json(result)
    assert payload["data"]["status"] == "conflict"
    assert payload["warnings"] == [_CONFLICT_NOTE]
    assert _json(_invoke(db, "show", paper, "--json"))["data"]["title"] is None


def test_paper_fetch_json_error(db: Path) -> None:
    result = _invoke(db, "paper", "fetch", str(uuid.uuid4()), "--json")

    assert result.exit_code == 1
    assert result.stdout.count("\n") == 1
    assert _json(result)["error"]["type"] == "PaperNotFound"


def test_init_reports_migration_failure_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic.util.exc import CommandError

    def fail_upgrade(engine):
        raise CommandError("Unknown revision")

    monkeypatch.setattr("litlattice.installation.upgrade_database", fail_upgrade)
    result = runner.invoke(app, ["--db", str(tmp_path / "test.db"), "init", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "DatabaseMigrationFailed"
    assert "Unknown revision" in payload["error"]["message"]
    assert "inspect the migration error" in payload["error"]["message"]
    assert "recreate" not in payload["error"]["message"]
