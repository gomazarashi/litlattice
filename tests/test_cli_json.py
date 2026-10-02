import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest
from pdf_factory import write_pdf
from typer.testing import CliRunner, Result

from litlattice.cli import app
from litlattice.paths import normalize_local_path
from litlattice.providers import ProviderWork

runner = CliRunner()


def work(openalex: str, doi: str | None = None, title: str | None = None):
    identifiers = [("openalex", openalex)]
    if doi:
        identifiers.append(("doi", doi))
    return ProviderWork(tuple(identifiers), title or openalex, 2020)


class FakeProvider:
    def __init__(
        self, works: Sequence[ProviderWork], cites: dict[str, list[str]]
    ) -> None:
        self.works = {w.identifiers[0][1]: w for w in works}
        self.cites = cites

    def lookup_work(self, identifiers):
        wanted = {(s, v.lower()) for s, v in identifiers}
        for w in self.works.values():
            if {(s, v.lower()) for s, v in w.identifiers} & wanted:
                return w
        return None

    def _id(self, w: ProviderWork) -> str:
        return w.identifiers[0][1]

    def fetch_references(self, w, *, limit):
        return [self.works[i] for i in self.cites.get(self._id(w), [])][:limit]

    def fetch_citations(self, w, *, limit):
        citing = [a for a, bs in self.cites.items() if self._id(w) in bs]
        return [self.works[i] for i in citing][:limit]


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


def test_show_json(db: Path) -> None:
    paper = _paper(db, "--doi", "10.1/s", "--title", "Seed")

    result = _invoke(db, "show", paper, "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["warnings"] == []
    data = payload["data"]
    assert data["id"] == paper
    assert data["title"] == "Seed"
    assert data["publication_year"] is None
    assert datetime.fromisoformat(data["created_at"]).tzinfo is not None
    assert data["identifiers"] == [
        {"scheme": "doi", "value": "10.1/s", "normalized_value": "10.1/s"}
    ]
    assert data["in_library"] is False


def test_show_json_short_option(db: Path) -> None:
    paper = _paper(db)

    result = _invoke(db, "show", paper, "-j")

    assert result.exit_code == 0, result.output
    assert _json(result)["data"]["id"] == paper


def test_paper_list_and_src_list_json(db: Path, tmp_path: Path) -> None:
    paper = _paper(db, "--doi", "10.1/s")
    directory = tmp_path / "papers"
    directory.mkdir()
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0

    papers = _json(_invoke(db, "paper", "list", "--json"))
    sources = _json(_invoke(db, "src", "list", "--json"))

    assert [item["id"] for item in papers["data"]] == [paper]
    assert [item["normalized_path"] for item in sources["data"]] == [
        str(normalize_local_path(directory))
    ]
    assert sources["data"][0]["last_scanned_at"] is None


def test_scan_json(db: Path, tmp_path: Path) -> None:
    directory = tmp_path / "papers"
    directory.mkdir()
    pdf = write_pdf(directory / "2401.12345v2.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0

    result = _invoke(db, "scan", "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    data = _json(result)["data"]
    assert data["sources"][0]["found"] == 1
    assert len(data["new_document_ids"]) == 1
    (identification,) = data["identifications"]
    assert identification["status"] == "created"
    assert identification["path"] == str(normalize_local_path(pdf))
    assert identification["paper_id"] is not None
    assert identification["candidate_paper_ids"] == []


def test_doc_list_json(db: Path, tmp_path: Path) -> None:
    directory = tmp_path / "papers"
    directory.mkdir()
    pdf = write_pdf(directory / "unknown.pdf")
    assert _invoke(db, "src", "add", str(directory)).exit_code == 0
    assert _invoke(db, "scan").exit_code == 0

    result = _invoke(db, "doc", "list", "--json")

    assert result.exit_code == 0, result.output
    (document,) = _json(result)["data"]
    assert document["path"] == str(normalize_local_path(pdf))
    assert document["paper_id"] is None
    assert document["missing_since"] is None
    assert datetime.fromisoformat(document["last_seen_at"]).tzinfo is not None


def test_expand_json(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = FakeProvider(
        [
            work("W1", "10.1/s", "Seed"),
            work("W2", "10.1/r1", "Ref one"),
            work("W3", "10.1/r2", "Ref two"),
        ],
        {"W1": ["W2", "W3"]},
    )
    monkeypatch.setattr("litlattice.cli._provider", lambda: graph)
    seed = _paper(db, "--doi", "10.1/s")

    result = _invoke(db, "expand", seed, "--direction", "references", "--json")

    assert result.exit_code == 0, result.output
    assert "Expanding" not in result.stdout
    payload = _json(result)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["seed_paper_id"] == seed
    assert data["seed_metadata"]["status"] == "updated"
    assert len(data["papers_created"]) == 2
    assert data["papers_matched"] == []
    assert data["citations_created"] == 2
    assert data["citations_confirmed"] == 0
    assert data["skipped"] == []
    assert data["truncated"] is False


def test_expand_json_truncation_is_a_warning(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = FakeProvider(
        [
            work("W1", "10.1/s", "Seed"),
            work("W2", "10.1/r1"),
            work("W3", "10.1/r2"),
        ],
        {"W1": ["W2", "W3"]},
    )
    monkeypatch.setattr("litlattice.cli._provider", lambda: graph)
    seed = _paper(db, "--doi", "10.1/s")

    result = _invoke(db, "expand", seed, "--max-nodes", "2", "--json")

    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["data"]["truncated"] is True
    assert (
        "Stopped at --max-nodes 2; run again with a larger value to continue."
        in payload["warnings"]
    )


def test_expand_json_unconfirmed_is_a_warning(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = ProviderWork(
        (("openalex", "W1"), ("doi", "10.1/published")), "Published", 2021
    )

    class ArxivToPublished(FakeProvider):
        def lookup_work(self, identifiers):
            return published

    monkeypatch.setattr(
        "litlattice.cli._provider", lambda: ArxivToPublished([published], {})
    )
    seed = _paper(db, "--arxiv", "2401.12345")

    result = _invoke(db, "expand", seed, "--json")

    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["data"]["seed_metadata"]["status"] == "unconfirmed"
    assert (
        "OpenAlex's record shares no identifier with this Paper, so it cannot be "
        "confirmed to be the same work; nothing was stored."
    ) in payload["warnings"]


def test_json_error_for_missing_paper(db: Path) -> None:
    unknown = uuid.uuid4()

    result = _invoke(db, "show", str(unknown), "--json")

    assert result.exit_code == 1
    assert result.stdout.count("\n") == 1
    assert _json(result) == {
        "ok": False,
        "error": {
            "type": "PaperNotFound",
            "message": f"Paper not found: {unknown}",
        },
    }


def test_json_error_for_uninitialized_database(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    result = _invoke(db_path, "paper", "list", "--json")

    assert result.exit_code == 1
    payload = _json(result)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "DatabaseNotInitialized"
    assert not db_path.exists()


def test_json_error_for_unknown_paper_in_doc_list(db: Path) -> None:
    result = _invoke(db, "doc", "list", "--paper", str(uuid.uuid4()), "--json")

    assert result.exit_code == 1
    assert _json(result)["error"]["type"] == "PaperNotFound"
