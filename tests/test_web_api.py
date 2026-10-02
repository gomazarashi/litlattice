import json
import uuid
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from litlattice.citations import add_citation
from litlattice.database import open_database
from litlattice.graph import GraphDirection, library_graph, neighborhood_graph
from litlattice.installation import initialize
from litlattice.papers import add_to_library, create_paper
from litlattice.serialization import to_json_value
from litlattice.web import create_app


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "api.db"
    initialize(path)
    return path


@pytest.fixture
def client(db_path: Path) -> FlaskClient:
    return create_app(db_path, secret_key="test-secret").test_client()


@pytest.fixture
def graph_data(db_path: Path) -> dict[str, uuid.UUID]:
    with open_database(db_path) as engine:
        seed = create_paper(engine, [("doi", "10.5555/seed")], title="Seed").id
        reference = create_paper(engine, [("doi", "10.5555/ref")], title="Reference").id
        deeper = create_paper(engine, title="Deeper").id
        citing = create_paper(engine, title="Citing").id
        outside = create_paper(engine, title="Outside").id
        add_to_library(engine, seed)
        add_to_library(engine, reference)
        add_citation(engine, seed, reference)
        add_citation(engine, reference, deeper)
        add_citation(engine, citing, seed)
        add_citation(engine, outside, seed)
    return {
        "seed": seed,
        "reference": reference,
        "deeper": deeper,
        "citing": citing,
        "outside": outside,
    }


def _json(response) -> dict:
    assert response.headers["Content-Type"] == "application/json"
    text = response.get_data(as_text=True)
    assert "<html" not in text
    return json.loads(text)


def _node_ids(data: dict) -> set[str]:
    return {node["paper"]["id"] for node in data["nodes"]}


def test_library_graph_api(client: FlaskClient, graph_data, db_path: Path) -> None:
    response = client.get("/api/graph/library")

    assert response.status_code == 200
    payload = _json(response)
    assert payload["ok"] is True
    assert payload["warnings"] == []
    data = payload["data"]
    assert data["kind"] == "library"
    assert data["seed_paper_id"] is None
    assert data["depth"] is None
    assert data["direction"] is None
    assert _node_ids(data) == {str(graph_data["seed"]), str(graph_data["reference"])}
    assert data["edges"] == [
        {
            "citing_paper_id": str(graph_data["seed"]),
            "cited_paper_id": str(graph_data["reference"]),
        }
    ]


def test_library_graph_api_empty_library(client: FlaskClient, db_path: Path) -> None:
    data = _json(client.get("/api/graph/library"))["data"]

    assert data["nodes"] == []
    assert data["edges"] == []


def test_library_graph_api_matches_cli_serialization(
    client: FlaskClient, graph_data, db_path: Path
) -> None:
    with open_database(db_path) as engine:
        expected = to_json_value(library_graph(engine))

    assert _json(client.get("/api/graph/library"))["data"] == expected


def test_neighborhood_graph_api_matches_cli_serialization(
    client: FlaskClient, graph_data, db_path: Path
) -> None:
    seed = graph_data["seed"]
    with open_database(db_path) as engine:
        expected = to_json_value(
            neighborhood_graph(engine, seed, depth=2, direction=GraphDirection.both)
        )

    response = client.get(f"/api/graph/neighborhood/{seed}?depth=2&direction=both")

    assert response.status_code == 200
    assert _json(response)["data"] == expected


def test_neighborhood_graph_api_defaults_to_depth_one_both(
    client: FlaskClient, graph_data
) -> None:
    data = _json(client.get(f"/api/graph/neighborhood/{graph_data['seed']}"))["data"]

    assert data["kind"] == "neighborhood"
    assert data["seed_paper_id"] == str(graph_data["seed"])
    assert data["depth"] == 1
    assert data["direction"] == "both"
    assert _node_ids(data) == {
        str(graph_data["seed"]),
        str(graph_data["reference"]),
        str(graph_data["citing"]),
        str(graph_data["outside"]),
    }


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("references", ("seed", "reference")),
        ("citations", ("seed", "citing", "outside")),
        ("both", ("seed", "reference", "citing", "outside")),
    ],
)
def test_neighborhood_graph_api_directions(
    client: FlaskClient, graph_data, direction: str, expected: tuple[str, ...]
) -> None:
    data = _json(
        client.get(
            f"/api/graph/neighborhood/{graph_data['seed']}?direction={direction}"
        )
    )["data"]

    assert data["direction"] == direction
    assert _node_ids(data) == {str(graph_data[name]) for name in expected}


def test_neighborhood_graph_api_depth_two(client: FlaskClient, graph_data) -> None:
    data = _json(
        client.get(
            f"/api/graph/neighborhood/{graph_data['seed']}?depth=2&direction=references"
        )
    )["data"]

    assert data["depth"] == 2
    assert {node["paper"]["id"]: node["distance"] for node in data["nodes"]} == {
        str(graph_data["seed"]): 0,
        str(graph_data["reference"]): 1,
        str(graph_data["deeper"]): 2,
    }


def test_neighborhood_graph_api_depth_zero(client: FlaskClient, graph_data) -> None:
    data = _json(client.get(f"/api/graph/neighborhood/{graph_data['seed']}?depth=0"))[
        "data"
    ]

    assert data["depth"] == 0
    assert _node_ids(data) == {str(graph_data["seed"])}


def test_neighborhood_graph_api_unknown_paper_is_404_json(
    client: FlaskClient, graph_data
) -> None:
    response = client.get(f"/api/graph/neighborhood/{uuid.uuid4()}")

    assert response.status_code == 404
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "PaperNotFound"


@pytest.mark.parametrize("query", ["depth=3", "depth=abc", "direction=sideways"])
def test_neighborhood_graph_api_invalid_parameters_is_400_json(
    client: FlaskClient, graph_data, query: str
) -> None:
    response = client.get(f"/api/graph/neighborhood/{graph_data['seed']}?{query}")

    assert response.status_code == 400
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "InvalidParameter"


def test_library_graph_api_uninitialized_database_is_503_json(
    tmp_path: Path,
) -> None:
    client = create_app(tmp_path / "missing.db", secret_key="test-secret").test_client()

    response = client.get("/api/graph/library")

    assert response.status_code == 503
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "DatabaseNotInitialized"
    assert not (tmp_path / "missing.db").exists()


def test_neighborhood_graph_api_invalid_uuid_is_404_json(
    client: FlaskClient,
) -> None:
    response = client.get("/api/graph/neighborhood/not-a-uuid")

    assert response.status_code == 404
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "NotFound"


def test_unknown_api_path_is_404_json(client: FlaskClient) -> None:
    response = client.get("/api/unknown")

    assert response.status_code == 404
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "NotFound"
