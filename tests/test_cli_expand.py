import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from litlattice.cli import app
from litlattice.database import open_database
from litlattice.errors import ProviderError
from litlattice.papers import create_paper
from litlattice.providers import ProviderWork

runner = CliRunner()


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
    """A provider over an in-memory citation graph: ``cites[a]`` = what a cites."""

    def __init__(
        self, works: Sequence[ProviderWork], cites: dict[str, list[str]]
    ) -> None:
        self.works = {w.identifiers[0][1]: w for w in works}
        self.cites = cites
        self.calls: list[tuple[str, str, int]] = []

    def lookup_work(self, identifiers):
        wanted = {(s, v.lower()) for s, v in identifiers}
        for w in self.works.values():
            if {(s, v.lower()) for s, v in w.identifiers} & wanted:
                return w
        return None

    def _id(self, w: ProviderWork) -> str:
        return w.identifiers[0][1]

    def fetch_references(self, w, *, limit):
        self.calls.append(("references", self._id(w), limit))
        return [self.works[i] for i in self.cites.get(self._id(w), [])][:limit]

    def fetch_citations(self, w, *, limit):
        self.calls.append(("citations", self._id(w), limit))
        citing = [a for a, bs in self.cites.items() if self._id(w) in bs]
        return [self.works[i] for i in citing][:limit]


@pytest.fixture
def graph() -> FakeProvider:
    # S cites R1, R2; C1 and C2 cite S.
    return FakeProvider(
        [
            work("W1", "10.1/s", "Seed", 2020),
            work("W2", "10.1/r1", "Ref one", 2019),
            work("W3", "10.1/r2", "Ref two", 2018),
            work("W4", None, "Citer one", 2021),
            work("W5", "10.1/c2", "Citer two", 2022),
        ],
        {"W1": ["W2", "W3"], "W4": ["W1"], "W5": ["W1"]},
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "litlattice.db"
    result = runner.invoke(app, ["--db", str(db_path), "init"])
    assert result.exit_code == 0, result.output
    return db_path


def _invoke(db: Path, *args: str) -> Result:
    return runner.invoke(app, ["--db", str(db), *args])


def _paper(db: Path, *options: str) -> str:
    result = _invoke(db, "paper", "create", *options)
    assert result.exit_code == 0, result.output
    return next(
        line.removeprefix("ID:").strip()
        for line in result.stdout.splitlines()
        if line.startswith("ID:")
    )


@pytest.fixture
def provider(graph: FakeProvider, monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    monkeypatch.setattr("litlattice.cli._provider", lambda: graph)
    return graph


def test_paper_fetch_fills_metadata(db: Path, provider: FakeProvider) -> None:
    seed = _paper(db, "--doi", "10.1/s")

    result = _invoke(db, "paper", "fetch", seed)

    assert result.exit_code == 0, result.output
    assert result.stderr == "Fetching metadata from OpenAlex...\n"
    assert "Metadata updated from OpenAlex.\n" in result.stdout
    assert "Added identifiers: openalex:W1\n" in result.stdout
    assert "Title:       Seed\n" in result.stdout
    assert "Year:        2020\n" in result.stdout
    assert "In library:  no\n" in result.stdout


def test_paper_fetch_unconfirmed_note(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = ProviderWork(
        (("openalex", "W1"), ("doi", "10.1/published")), "Published", 2021
    )

    class ArxivToPublished(FakeProvider):
        def lookup_work(self, identifiers):
            return published

    fake = ArxivToPublished([published], {})
    monkeypatch.setattr("litlattice.cli._provider", lambda: fake)
    seed = _paper(db, "--arxiv", "2401.12345")

    result = _invoke(db, "paper", "fetch", seed)

    assert result.exit_code == 0, result.output
    assert (
        "OpenAlex's record shares no identifier with this Paper, so it cannot be "
        "confirmed to be the same work; its metadata was not applied.\n"
        in result.stdout
    )
    assert "Added identifiers" not in result.stdout
    assert "Title:       (untitled)\n" in result.stdout


def test_paper_fetch_conflict_warns(db: Path, provider: FakeProvider) -> None:
    seed = _paper(db, "--doi", "10.1/s")
    with open_database(db) as engine:
        create_paper(engine, [("openalex", "W1")])

    result = _invoke(db, "paper", "fetch", seed)

    assert result.exit_code == 0, result.output
    assert "Metadata updated from OpenAlex." not in result.stdout
    assert (
        "Warning: OpenAlex's record also matches another Paper; nothing was changed.\n"
        in result.stderr
    )


def test_expand_reports_papers_and_citations(db: Path, provider: FakeProvider) -> None:
    seed = _paper(db, "--doi", "10.1/s")

    result = _invoke(db, "expand", seed, "--direction", "references")

    assert result.exit_code == 0, result.output
    assert result.stderr == (
        "Expanding from OpenAlex (direction=references, depth=1, max nodes=200)...\n"
    )
    assert result.stdout.splitlines() == [
        f"Expanded {seed}",
        "Papers: 2 new, 0 existing",
        "Citations: 2 new, 0 confirmed",
        f"Not added to the Library. Review with `llat citation list {seed}` or the web UI.",
    ]


def test_expand_references_are_outgoing_and_citations_incoming(
    db: Path, provider: FakeProvider
) -> None:
    seed = _paper(db, "--doi", "10.1/s")
    assert _invoke(db, "expand", seed, "--direction", "citations").exit_code == 0

    references = _invoke(db, "citation", "list", seed, "--direction", "references")
    citations = _invoke(db, "citation", "list", seed, "--direction", "citations")

    assert references.stdout == ""
    lines = citations.stdout.splitlines()
    assert len(lines) == 2
    assert all(line.startswith("<- ") for line in lines)
    assert all("Citer one" in line or "Citer two" in line for line in lines)


def test_expand_does_not_add_to_library(db: Path, provider: FakeProvider) -> None:
    seed = _paper(db, "--doi", "10.1/s")

    assert _invoke(db, "expand", seed).exit_code == 0

    lines = _invoke(db, "paper", "list").stdout.splitlines()
    assert len(lines) == 5
    assert all("  L  " not in line for line in lines)


def test_expand_reports_skipped_works(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conflicting = ProviderWork(
        (("openalex", "W2"), ("doi", "10.1/r1"), ("doi", "10.2/r2")), "Twin", 2019
    )
    fake = FakeProvider([work("W1", "10.1/s"), conflicting], {"W1": ["W2"]})
    monkeypatch.setattr("litlattice.cli._provider", lambda: fake)
    seed = _paper(db, "--doi", "10.1/s")
    _paper(db, "--doi", "10.1/r1")
    _paper(db, "--doi", "10.2/r2")

    result = _invoke(db, "expand", seed, "--direction", "references")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"Expanded {seed}",
        "Papers: 0 new, 0 existing",
        "Citations: 0 new, 0 confirmed",
        "Skipped 1 work(s) matching several Papers:",
        "  Twin  openalex:W2 doi:10.1/r1 doi:10.2/r2",
        f"Not added to the Library. Review with `llat citation list {seed}` or the web UI.",
    ]


def test_expand_reports_truncation_on_stderr(db: Path, provider: FakeProvider) -> None:
    seed = _paper(db, "--doi", "10.1/s")

    result = _invoke(db, "expand", seed, "--max-nodes", "2")

    assert result.exit_code == 0, result.output
    assert (
        "Stopped at --max-nodes 2; run again with a larger value to continue.\n"
        in result.stderr
    )


def test_expand_rejects_invalid_options(db: Path, provider: FakeProvider) -> None:
    seed = _paper(db, "--doi", "10.1/s")

    assert _invoke(db, "expand", seed, "--depth", "0").exit_code == 2
    assert _invoke(db, "expand", seed, "--max-nodes", "0").exit_code == 2
    assert _invoke(db, "expand", seed, "--direction", "sideways").exit_code == 2


def test_expand_provider_error_is_reported(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingProvider(FakeProvider):
        def lookup_work(self, identifiers):
            raise ProviderError("OpenAlex request failed")

    monkeypatch.setattr("litlattice.cli._provider", lambda: FailingProvider([], {}))
    seed = _paper(db, "--doi", "10.1/s")

    result = _invoke(db, "expand", seed)

    assert result.exit_code == 1
    assert "Error: OpenAlex request failed" in result.stderr
    assert _invoke(db, "citation", "list", seed).stdout == ""


def test_fetch_unknown_paper_is_reported(db: Path, provider: FakeProvider) -> None:
    result = _invoke(db, "paper", "fetch", str(uuid.uuid4()))

    assert result.exit_code == 1
    assert "Error: Paper not found" in result.stderr
