import json
import uuid
from collections.abc import Sequence
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner, Result

from litlattice.cli import app
from litlattice.providers import ProviderWork

runner = CliRunner()

AMBIGUOUS_NOTE = (
    "These identifiers belong to several existing Papers; nothing was changed."
)
UNCONFIRMED_NOTE = (
    "OpenAlex's record shares no identifier with the given ones, so it cannot be "
    "confirmed to be the same work; nothing was stored."
)
IGNORED_NOTE = (
    "Not attached because OpenAlex's record does not report them "
    "(they may denote another version of the paper)"
)


def work(
    openalex: str,
    doi: str | None = None,
    arxiv: str | None = None,
    title: str | None = None,
    year: int | None = None,
) -> ProviderWork:
    identifiers = [("openalex", openalex)]
    if doi:
        identifiers.append(("doi", doi))
    if arxiv:
        identifiers.append(("arxiv", arxiv))
    return ProviderWork(tuple(identifiers), title or openalex, year)


class FakeProvider:
    def __init__(
        self,
        works: Sequence[ProviderWork],
        results: Sequence[ProviderWork] | None = None,
    ) -> None:
        self.works = list(works)
        self.results = list(works if results is None else results)
        self.search_calls: list[tuple[str, int]] = []

    def lookup_work(self, identifiers):
        wanted = {(scheme, value.lower()) for scheme, value in identifiers}
        for candidate in self.works:
            keys = {(scheme, value.lower()) for scheme, value in candidate.identifiers}
            if keys & wanted:
                return candidate
        return None

    def search_works(self, query, *, limit):
        self.search_calls.append((query, limit))
        return self.results[:limit]


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


def _use_provider(
    monkeypatch: pytest.MonkeyPatch,
    works: Sequence[ProviderWork],
    results: Sequence[ProviderWork] | None = None,
) -> FakeProvider:
    fake = FakeProvider(works, results)
    monkeypatch.setattr("litlattice.cli._provider", lambda: fake)
    return fake


def test_paper_import_doi_created_then_matched(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [work("W1", "10.1/s", title="Seed", year=2020)])

    first = _invoke(db, "paper", "import", "--doi", "10.1/s")

    assert first.exit_code == 0, first.output
    assert first.stderr == "Fetching from OpenAlex...\n"
    lines = first.stdout.splitlines()
    assert lines[0] == "Imported a new Paper."
    assert "Title:       Seed" in lines
    assert "Year:        2020" in lines
    assert "In library:  no" in lines
    assert "  doi:10.1/s" in lines
    assert "  openalex:W1" in lines
    paper_id = next(
        line.removeprefix("ID:").strip() for line in lines if line.startswith("ID:")
    )
    assert lines[-1] == (
        f"Not added to the Library. Add it with `llat lib add {paper_id}`."
    )

    listed = _invoke(db, "paper", "list")
    assert len(listed.stdout.splitlines()) == 1
    assert "  -  " in listed.stdout

    second = _invoke(db, "paper", "import", "--doi", "10.1/s")

    assert second.exit_code == 0, second.output
    assert second.stdout.splitlines()[0] == "Matched an existing Paper."
    assert "Added identifiers" not in second.stdout
    assert (
        f"Not added to the Library. Add it with `llat lib add {paper_id}`."
        in second.stdout
    )
    assert len(_invoke(db, "paper", "list").stdout.splitlines()) == 1


def test_paper_import_arxiv_and_openalex(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(
        monkeypatch,
        [
            work("W2", arxiv="2401.12345", title="Preprint", year=2024),
            work("W3", title="Other"),
        ],
    )

    arxiv = _invoke(db, "paper", "import", "--arxiv", "2401.12345")
    openalex = _invoke(db, "paper", "import", "--openalex", "W3")

    assert arxiv.exit_code == 0, arxiv.output
    assert "Imported a new Paper." in arxiv.stdout
    assert "  arxiv:2401.12345" in arxiv.stdout
    assert "  openalex:W2" in arxiv.stdout
    assert openalex.exit_code == 0, openalex.output
    assert "Imported a new Paper." in openalex.stdout
    assert "  openalex:W3" in openalex.stdout
    assert len(_invoke(db, "paper", "list").stdout.splitlines()) == 2


def test_paper_import_matched_adds_identifiers(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [work("W1", "10.1/s", title="Seed", year=2020)])
    paper_id = _paper(db, "--doi", "10.1/s")

    first = _invoke(db, "paper", "import", "--doi", "10.1/s")

    assert first.exit_code == 0, first.output
    lines = first.stdout.splitlines()
    assert lines[0] == "Matched an existing Paper."
    assert lines[1] == "Added identifiers: openalex:W1"
    assert f"Not added to the Library. Add it with `llat lib add {paper_id}`." in lines

    assert _invoke(db, "lib", "add", paper_id).exit_code == 0
    second = _invoke(db, "paper", "import", "--doi", "10.1/s")

    assert second.exit_code == 0, second.output
    assert "In library:  yes" in second.stdout
    assert "Added identifiers" not in second.stdout
    assert "Not added to the Library." not in second.stdout


def test_paper_import_json(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, [work("W1", "10.1/s", title="Seed", year=2020)])

    result = _invoke(db, "paper", "import", "--doi", "10.1/s", "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["warnings"] == []
    data = payload["data"]
    assert data["status"] == "created"
    assert data["title"] == "Seed"
    assert data["publication_year"] == 2020
    assert data["paper_id"] is not None
    assert data["identifiers_ignored"] == []
    paper_id = uuid.UUID(data["paper_id"])

    short = _invoke(db, "paper", "import", "--openalex", "W1", "-j")

    assert short.exit_code == 0, short.output
    assert short.stderr == ""
    matched = _json(short)["data"]
    assert matched["status"] == "matched"
    assert matched["paper_id"] == str(paper_id)


def test_paper_import_ambiguous_changes_nothing(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    twin = ProviderWork(
        (("openalex", "W9"), ("doi", "10.1/a"), ("doi", "10.2/b")), "Twin", 2020
    )
    _use_provider(monkeypatch, [twin])
    first = _paper(db, "--doi", "10.1/a")
    second = _paper(db, "--doi", "10.2/b")

    result = _invoke(db, "paper", "import", "--doi", "10.1/a")

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == ["Fetching from OpenAlex...", AMBIGUOUS_NOTE]
    candidates = sorted([first, second])
    assert result.stdout.splitlines() == [
        "Candidates:",
        f"  {candidates[0]}",
        f"  {candidates[1]}",
    ]
    assert len(_invoke(db, "paper", "list").stdout.splitlines()) == 2

    json_result = _invoke(db, "paper", "import", "--doi", "10.1/a", "--json")

    assert json_result.exit_code == 0, json_result.output
    payload = _json(json_result)
    assert payload["data"]["status"] == "ambiguous"
    assert payload["data"]["paper_id"] is None
    assert AMBIGUOUS_NOTE in payload["warnings"]


def test_paper_import_unconfirmed_changes_nothing(
    db: Path, monkeypatch: pytest.MonkeyPatch
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

    result = _invoke(db, "paper", "import", "--arxiv", "2401.12345")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "Fetching from OpenAlex...",
        UNCONFIRMED_NOTE,
    ]
    assert _invoke(db, "paper", "list").stdout == ""

    json_result = _invoke(db, "paper", "import", "--arxiv", "2401.12345", "--json")

    assert json_result.exit_code == 0, json_result.output
    payload = _json(json_result)
    assert payload["data"]["status"] == "unconfirmed"
    assert UNCONFIRMED_NOTE in payload["warnings"]


def test_paper_import_does_not_attach_unreported_identifiers(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [work("W1", "10.1/s", title="Seed", year=2020)])

    result = _invoke(db, "paper", "import", "--doi", "10.1/s", "--arxiv", "2401.12345")

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        "Fetching from OpenAlex...",
        f"{IGNORED_NOTE}: arxiv:2401.12345",
    ]
    paper_id = next(
        line.removeprefix("ID:").strip()
        for line in result.stdout.splitlines()
        if line.startswith("ID:")
    )
    shown = _invoke(db, "show", paper_id)
    assert "doi:10.1/s" in shown.stdout
    assert "arxiv" not in shown.stdout


def test_paper_import_unknown_work(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, [])

    result = _invoke(db, "paper", "import", "--doi", "10.1/missing")

    assert result.exit_code == 1
    assert "Error: Provider has no record of doi:10.1/missing" in result.stderr
    assert _invoke(db, "paper", "list").stdout == ""


def test_paper_import_unknown_work_json(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [])

    result = _invoke(db, "paper", "import", "--doi", "10.1/missing", "--json")

    assert result.exit_code == 1
    assert result.stdout.count("\n") == 1
    payload = _json(result)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "WorkNotFound"


def test_paper_import_invalid_doi(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, [])

    result = _invoke(db, "paper", "import", "--doi", "not-a-doi")

    assert result.exit_code == 1
    assert "Error: Invalid doi identifier" in result.stderr
    assert _invoke(db, "paper", "list").stdout == ""

    json_result = _invoke(db, "paper", "import", "--doi", "not-a-doi", "--json")

    assert json_result.exit_code == 1
    assert _json(json_result)["error"]["type"] == "InvalidIdentifier"


def test_paper_import_requires_an_identifier(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [])

    result = _invoke(db, "paper", "import")

    assert result.exit_code == 2


def test_search_lists_new_and_existing(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = _paper(db, "--doi", "10.1/known")
    provider = _use_provider(
        monkeypatch,
        [
            work("W1", "10.1/known", title="Known work", year=2019),
            work("W2", "10.1/fresh", title="Fresh work", year=2021),
        ],
    )

    result = _invoke(db, "search", "graph")

    assert result.exit_code == 0, result.output
    assert result.stderr == "Searching OpenAlex...\n"
    assert result.stdout.splitlines() == [
        f"{'existing':<9} Known work (2019)  openalex:W1 doi:10.1/known  -> {existing}",
        f"{'new':<9} Fresh work (2021)  openalex:W2 doi:10.1/fresh",
        (
            "Import one with `llat paper import --openalex <ID>` "
            "(nothing is stored until you do)."
        ),
    ]
    assert provider.search_calls == [("graph", 10)]


def test_search_passes_limit_to_provider(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _use_provider(monkeypatch, [work("W1", "10.1/a", "Alpha", 2020)])

    result = _invoke(db, "search", "alpha", "--limit", "3")

    assert result.exit_code == 0, result.output
    assert provider.search_calls == [("alpha", 3)]


def test_search_json(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _paper(db, "--doi", "10.1/known")
    _use_provider(
        monkeypatch,
        [
            work("W1", "10.1/known", title="Known work", year=2019),
            work("W2", "10.1/fresh", title="Fresh work", year=2021),
        ],
    )

    result = _invoke(db, "search", "graph", "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    payload = _json(result)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["query"] == "graph"
    assert [candidate["status"] for candidate in data["candidates"]] == [
        "existing",
        "new",
    ]
    assert data["candidates"][0]["paper_id"] == existing
    assert data["candidates"][1]["paper_id"] is None


def test_search_without_candidates(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _use_provider(monkeypatch, [])

    result = _invoke(db, "search", "nothing")

    assert result.exit_code == 0, result.output
    assert result.stdout == "No works found.\n"
    assert provider.search_calls == [("nothing", 10)]


def test_search_rejects_empty_query_and_bad_limit(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_provider(monkeypatch, [])

    assert _invoke(db, "search", "   ").exit_code == 2
    assert _invoke(db, "search", "x", "--limit", "0").exit_code == 2
    assert _invoke(db, "search", "x", "--limit", "51").exit_code == 2


def test_help_lists_import_and_search() -> None:
    root = runner.invoke(app, ["--help"])

    assert root.exit_code == 0
    assert "search" in click.unstyle(root.output)

    paper = runner.invoke(app, ["paper", "--help"])

    assert paper.exit_code == 0
    assert "import" in click.unstyle(paper.output)
