import json
import uuid
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner, Result

from litlattice.cli import app

runner = CliRunner()


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


def _cite(db: Path, citing: str, cited: str) -> None:
    result = _invoke(db, "citation", "add", citing, cited)
    assert result.exit_code == 0, result.output


def _lib(db: Path, paper: str) -> None:
    result = _invoke(db, "lib", "add", paper)
    assert result.exit_code == 0, result.output


def test_graph_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["graph", "--help"])

    assert result.exit_code == 0
    output = click.unstyle(result.output)
    assert "neighborhood" in output
    assert "library" in output


def test_graph_neighborhood_human(db: Path) -> None:
    seed = _paper(db, "--title", "Seed")
    referenced = _paper(db, "--title", "Referenced")
    citing = _paper(db, "--title", "Citing")
    _cite(db, seed, referenced)
    _cite(db, citing, seed)

    result = _invoke(db, "graph", "neighborhood", seed)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == f"Neighborhood of {seed} (direction=both, depth=1)"
    assert lines[1] == (
        "3 papers, 2 citations. Degrees and PageRank are computed within this graph."
    )
    assert "Papers:" in lines
    assert f"  d0 {seed}  in 1  out 1  pagerank" in result.stdout
    assert f"  d1 {referenced}  in 1  out 0  pagerank" in result.stdout
    assert f"  d1 {citing}  in 0  out 1  pagerank" in result.stdout
    assert "Citations (A -> B: A cites B):" in lines
    assert f"  {seed} -> {referenced}" in lines
    assert f"  {citing} -> {seed}" in lines


def test_graph_neighborhood_references_only_walks_outgoing(db: Path) -> None:
    seed = _paper(db, "--title", "Seed")
    referenced = _paper(db, "--title", "Referenced")
    citing = _paper(db, "--title", "Citing")
    _cite(db, seed, referenced)
    _cite(db, citing, seed)

    result = _invoke(db, "graph", "neighborhood", seed, "--direction", "references")

    assert result.exit_code == 0, result.output
    assert "(direction=references, depth=1)" in result.stdout
    assert f"d1 {referenced}" in result.stdout
    assert str(citing) not in result.stdout
    assert f"  {seed} -> {referenced}" in result.stdout


def test_graph_neighborhood_citations_only_walks_incoming(db: Path) -> None:
    seed = _paper(db, "--title", "Seed")
    referenced = _paper(db, "--title", "Referenced")
    citing = _paper(db, "--title", "Citing")
    _cite(db, seed, referenced)
    _cite(db, citing, seed)

    result = _invoke(db, "graph", "neighborhood", seed, "--direction", "citations")

    assert result.exit_code == 0, result.output
    assert "(direction=citations, depth=1)" in result.stdout
    assert f"d1 {citing}" in result.stdout
    assert str(referenced) not in result.stdout
    assert f"  {citing} -> {seed}" in result.stdout


def test_graph_neighborhood_without_citations_shows_none(db: Path) -> None:
    seed = _paper(db)

    result = _invoke(db, "graph", "neighborhood", seed, "--depth", "0")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    heading = lines.index("Citations (A -> B: A cites B):")
    assert lines[heading + 1] == "  (none)"


@pytest.mark.parametrize("flag", ["--json", "-j"])
def test_graph_neighborhood_json(db: Path, flag: str) -> None:
    seed = _paper(db, "--title", "Seed")
    referenced = _paper(db, "--title", "Referenced")
    citing = _paper(db, "--title", "Citing")
    _cite(db, seed, referenced)
    _cite(db, citing, seed)

    result = _invoke(db, "graph", "neighborhood", seed, flag)

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["warnings"] == []
    data = payload["data"]
    assert data["kind"] == "neighborhood"
    assert data["seed_paper_id"] == seed
    assert data["depth"] == 1
    assert data["direction"] == "both"
    nodes = {node["paper"]["id"]: node for node in data["nodes"]}
    assert set(nodes) == {seed, referenced, citing}
    assert nodes[seed]["distance"] == 0
    assert nodes[referenced]["distance"] == 1
    assert nodes[citing]["distance"] == 1
    assert nodes[seed]["in_degree"] == 1
    assert nodes[seed]["out_degree"] == 1
    assert nodes[referenced]["in_degree"] == 1
    assert nodes[referenced]["out_degree"] == 0
    assert nodes[citing]["in_degree"] == 0
    assert nodes[citing]["out_degree"] == 1
    assert all(isinstance(node["pagerank"], float) for node in data["nodes"])
    assert data["edges"] == [
        {"citing_paper_id": seed, "cited_paper_id": referenced},
        {"citing_paper_id": citing, "cited_paper_id": seed},
    ]


def test_graph_neighborhood_depth_two_references_json(db: Path) -> None:
    first = _paper(db)
    second = _paper(db)
    third = _paper(db)
    _cite(db, first, second)
    _cite(db, second, third)

    result = _invoke(
        db,
        "graph",
        "neighborhood",
        first,
        "--depth",
        "2",
        "--direction",
        "references",
        "--json",
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    data = _json(result)["data"]
    assert data["depth"] == 2
    assert data["direction"] == "references"
    assert {node["paper"]["id"]: node["distance"] for node in data["nodes"]} == {
        first: 0,
        second: 1,
        third: 2,
    }
    assert data["edges"] == [
        {"citing_paper_id": first, "cited_paper_id": second},
        {"citing_paper_id": second, "cited_paper_id": third},
    ]


def test_graph_library_human_and_json(db: Path) -> None:
    first = _paper(db, "--title", "First")
    second = _paper(db, "--title", "Second")
    outside = _paper(db, "--title", "Outside")
    _lib(db, first)
    _lib(db, second)
    _cite(db, first, second)
    _cite(db, first, outside)
    _cite(db, outside, second)

    human = _invoke(db, "graph", "library")

    assert human.exit_code == 0, human.output
    lines = human.stdout.splitlines()
    assert lines[0] == "Library graph"
    assert lines[1] == (
        "2 papers, 1 citations. Degrees and PageRank are computed within this graph."
    )
    assert f"  {first}  in 0  out 1  pagerank" in human.stdout
    assert f"  {second}  in 1  out 0  pagerank" in human.stdout
    assert f"  {first} -> {second}" in lines
    assert str(outside) not in human.stdout

    result = _invoke(db, "graph", "library", "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    data = _json(result)["data"]
    assert data["kind"] == "library"
    assert data["seed_paper_id"] is None
    assert data["depth"] is None
    assert data["direction"] is None
    assert {node["paper"]["id"] for node in data["nodes"]} == {first, second}
    assert all(node["distance"] is None for node in data["nodes"])
    assert data["edges"] == [{"citing_paper_id": first, "cited_paper_id": second}]


def test_graph_library_empty(db: Path) -> None:
    human = _invoke(db, "graph", "library")

    assert human.exit_code == 0, human.output
    lines = human.stdout.splitlines()
    assert lines[0] == "Library graph"
    assert lines[1] == (
        "0 papers, 0 citations. Degrees and PageRank are computed within this graph."
    )
    assert lines[2] == "The Library is empty."
    assert "Papers:" not in human.stdout
    assert "Citations" not in human.stdout

    result = _invoke(db, "graph", "library", "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.count("\n") == 1
    data = _json(result)["data"]
    assert data["kind"] == "library"
    assert data["nodes"] == []
    assert data["edges"] == []


def test_graph_neighborhood_unknown_paper_human(db: Path) -> None:
    unknown = uuid.uuid4()

    result = _invoke(db, "graph", "neighborhood", str(unknown))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: Paper not found" in (result.stderr or "")


def test_graph_neighborhood_unknown_paper_json(db: Path) -> None:
    unknown = uuid.uuid4()

    result = _invoke(db, "graph", "neighborhood", str(unknown), "--json")

    assert result.exit_code == 1
    assert result.stdout.count("\n") == 1
    assert _json(result) == {
        "ok": False,
        "error": {
            "type": "PaperNotFound",
            "message": f"Paper not found: {unknown}",
        },
    }


def test_graph_neighborhood_negative_depth_is_usage_error(db: Path) -> None:
    seed = _paper(db)

    result = _invoke(db, "graph", "neighborhood", seed, "--depth", "-1")

    assert result.exit_code == 2


def test_graph_library_uninitialized_database_json(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    result = _invoke(db_path, "graph", "library", "--json")

    assert result.exit_code == 1
    assert result.stdout.count("\n") == 1
    payload = _json(result)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "DatabaseNotInitialized"
    assert not db_path.exists()


def test_graph_json_stdout_is_one_json_line(db: Path) -> None:
    seed = _paper(db)
    referenced = _paper(db)
    _cite(db, seed, referenced)

    result = _invoke(db, "graph", "neighborhood", seed, "-d", "1", "--json")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["ok"] is True
