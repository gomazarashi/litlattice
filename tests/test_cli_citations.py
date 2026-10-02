import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from litlattice.cli import app

runner = CliRunner()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "litlattice.db"
    assert runner.invoke(app, ["--db", str(db_path), "init"]).exit_code == 0
    return db_path


def _invoke(db: Path, *args: str) -> Result:
    return runner.invoke(app, ["--db", str(db), *args])


def _paper(db: Path, title: str, *options: str) -> str:
    result = _invoke(db, "paper", "create", "--title", title, *options)
    assert result.exit_code == 0, result.output
    return next(
        line.removeprefix("ID:").strip()
        for line in result.stdout.splitlines()
        if line.startswith("ID:")
    )


def _cite(db: Path, citing: str, cited: str) -> Result:
    return _invoke(db, "citation", "add", citing, cited)


def test_paper_create_with_title(db: Path) -> None:
    result = _invoke(db, "paper", "create", "--title", "Graph Paper", "--doi", "10.1/A")

    assert result.exit_code == 0, result.output
    assert "Title:       Graph Paper\n" in result.stdout
    listing = _invoke(db, "paper", "list").stdout
    assert listing.rstrip().endswith("-  Graph Paper  doi:10.1/a")


def test_untitled_paper_is_shown_as_untitled(db: Path) -> None:
    result = _invoke(db, "paper", "create")

    assert "Title:       (untitled)\n" in result.stdout


def test_citation_add_is_idempotent(db: Path) -> None:
    a, b = _paper(db, "A"), _paper(db, "B")

    first = _cite(db, a, b)
    second = _cite(db, a, b)

    assert first.exit_code == 0
    assert first.stdout == f"Added citation: {a} -> {b}\n"
    assert second.exit_code == 0
    assert second.stdout == f"Citation already recorded: {a} -> {b}\n"


def test_citation_add_errors(db: Path) -> None:
    a = _paper(db, "A")

    self_cite = _cite(db, a, a)
    unknown = _cite(db, a, str(uuid.uuid4()))
    malformed = _cite(db, a, "not-a-uuid")

    assert self_cite.exit_code == 1
    assert "cannot cite itself" in self_cite.stderr
    assert unknown.exit_code == 1
    assert "not found" in unknown.stderr
    assert malformed.exit_code == 2


def test_citation_list_directions(db: Path) -> None:
    a, b = _paper(db, "Alpha"), _paper(db, "Beta")
    _cite(db, a, b)

    refs = _invoke(db, "citation", "list", a, "--direction", "references")
    cited_by = _invoke(db, "citation", "list", b, "--direction", "citations")
    a_cited_by = _invoke(db, "citation", "list", a, "--direction", "citations")

    assert refs.stdout == f"-> {b}  Beta\n"
    assert cited_by.stdout == f"<- {a}  Alpha\n"
    assert a_cited_by.stdout == ""


def test_citation_list_both_shows_references_then_citations(db: Path) -> None:
    a, b, c = _paper(db, "A"), _paper(db, "B"), _paper(db, "C")
    _cite(db, a, b)
    _cite(db, b, c)

    result = _invoke(db, "citation", "list", b)

    assert result.stdout.splitlines() == [f"-> {c}  C", f"<- {a}  A"]


def test_citation_list_rejects_unknown_direction(db: Path) -> None:
    a = _paper(db, "A")

    assert _invoke(db, "citation", "list", a, "--direction", "up").exit_code == 2


def test_path_follows_citation_direction(db: Path) -> None:
    a, b, c = _paper(db, "A"), _paper(db, "B"), _paper(db, "C")
    _cite(db, a, b)
    _cite(db, b, c)

    forward = _invoke(db, "path", a, c)
    backward = _invoke(db, "path", c, a)

    assert forward.exit_code == 0
    assert forward.stdout.splitlines() == [
        "Citation path (2 hops):",
        f"   {a}  A",
        f"-> {b}  B",
        f"-> {c}  C",
    ]
    assert backward.exit_code == 1
    assert "No citation path" in backward.stderr


def test_path_to_self(db: Path) -> None:
    a = _paper(db, "A")

    result = _invoke(db, "path", a, a)

    assert result.exit_code == 0
    assert result.stdout.splitlines()[0] == "Citation path (0 hops):"


def test_citation_does_not_change_library_membership(db: Path) -> None:
    a, b = _paper(db, "A"), _paper(db, "B")
    _invoke(db, "lib", "add", a)

    _cite(db, a, b)

    assert _invoke(db, "paper", "list").stdout.splitlines() == [
        f"{a}  L  A",
        f"{b}  -  B",
    ]
