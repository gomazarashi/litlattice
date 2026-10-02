import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

import pytest

from litlattice.citations import CitationEdge
from litlattice.errors import PaperNotFound
from litlattice.graph import CitationGraph, GraphDirection, GraphKind, GraphNode
from litlattice.papers import Identifier, PaperRecord
from litlattice.serialization import (
    error_envelope,
    failure_envelope,
    success_envelope,
    to_json_value,
)


class Scheme(StrEnum):
    doi = "doi"


@dataclass(frozen=True)
class Inner:
    name: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Outer:
    inner: Inner
    seen_at: datetime


@pytest.mark.parametrize("value", ["text", 1, 1.5, True, None])
def test_primitives_pass_through(value: object) -> None:
    assert to_json_value(value) == value


def test_uuid_becomes_str() -> None:
    paper_id = uuid.uuid4()
    assert to_json_value(paper_id) == str(paper_id)


def test_path_becomes_str() -> None:
    path = Path("/papers/2401.12345.pdf")
    assert to_json_value(path) == "/papers/2401.12345.pdf"


def test_aware_datetime_becomes_isoformat() -> None:
    value = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=9)))
    assert to_json_value(value) == "2024-01-02T03:04:05+09:00"


def test_enum_becomes_value() -> None:
    assert to_json_value(Scheme.doi) == "doi"
    assert to_json_value([Scheme.doi]) == ["doi"]


def test_list_and_tuple_become_lists() -> None:
    result = to_json_value([1, (2, 3)])

    assert result == [1, [2, 3]]
    assert isinstance(result, list)
    assert isinstance(result[1], list)


def test_nested_dataclass_is_converted_recursively() -> None:
    value = Outer(
        inner=Inner(name="seed", tags=("a", "b")),
        seen_at=datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC),
    )

    assert to_json_value(value) == {
        "inner": {"name": "seed", "tags": ["a", "b"]},
        "seen_at": "2024-05-06T07:08:09+00:00",
    }


def test_dataclass_class_is_not_converted() -> None:
    assert to_json_value(Outer) is Outer


def test_paper_record_regression() -> None:
    paper_id = uuid.uuid4()
    record = PaperRecord(
        id=paper_id,
        title="Seed",
        publication_year=2024,
        created_at=datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC),
        identifiers=(
            Identifier(scheme="doi", value="10.1/s", normalized_value="10.1/s"),
        ),
        in_library=True,
    )

    assert to_json_value(record) == {
        "id": str(paper_id),
        "title": "Seed",
        "publication_year": 2024,
        "created_at": "2026-09-30T12:00:00+00:00",
        "identifiers": [
            {"scheme": "doi", "value": "10.1/s", "normalized_value": "10.1/s"}
        ],
        "in_library": True,
    }


def test_success_envelope() -> None:
    envelope = success_envelope(Inner(name="seed", tags=("a", "b")), ["careful"])

    assert envelope == {
        "ok": True,
        "data": {"name": "seed", "tags": ["a", "b"]},
        "warnings": ["careful"],
    }


def test_success_envelope_defaults_to_no_warnings() -> None:
    assert success_envelope(None)["warnings"] == []


def test_success_envelope_copies_warnings_into_a_list() -> None:
    envelope = success_envelope([], ("one", "two"))

    assert envelope["warnings"] == ["one", "two"]
    assert isinstance(envelope["warnings"], list)


def test_error_envelope() -> None:
    paper_id = uuid.uuid4()

    envelope = error_envelope(PaperNotFound(paper_id))

    assert envelope == {
        "ok": False,
        "error": {
            "type": "PaperNotFound",
            "message": f"Paper not found: {paper_id}",
        },
    }


def test_failure_envelope() -> None:
    envelope = failure_envelope("InvalidParameter", "depth must be 0, 1 or 2")

    assert envelope == {
        "ok": False,
        "error": {
            "type": "InvalidParameter",
            "message": "depth must be 0, 1 or 2",
        },
    }


def test_envelopes_are_json_dumpable() -> None:
    paper_id = uuid.uuid4()
    payloads = [
        success_envelope(Inner(name="seed", tags=("a", "b")), ["warning"]),
        error_envelope(PaperNotFound(paper_id)),
        failure_envelope("InvalidParameter", "depth must be 0, 1 or 2"),
    ]

    for payload in payloads:
        assert json.loads(json.dumps(payload)) == payload


def test_citation_graph() -> None:
    citing, cited = uuid.uuid4(), uuid.uuid4()
    created_at = datetime(2026, 10, 1, tzinfo=UTC)

    def node(paper_id: uuid.UUID, distance: int, in_degree: int, out_degree: int):
        record = PaperRecord(paper_id, None, None, created_at, (), False)
        return GraphNode(record, distance, in_degree, out_degree, 0.5)

    graph = CitationGraph(
        kind=GraphKind.neighborhood,
        seed_paper_id=citing,
        depth=1,
        direction=GraphDirection.references,
        nodes=(node(citing, 0, 0, 1), node(cited, 1, 1, 0)),
        edges=(CitationEdge(citing, cited),),
    )

    value = to_json_value(graph)

    assert value["kind"] == "neighborhood"
    assert value["seed_paper_id"] == str(citing)
    assert value["depth"] == 1
    assert value["direction"] == "references"
    assert value["nodes"][0] == {
        "paper": {
            "id": str(citing),
            "title": None,
            "publication_year": None,
            "created_at": "2026-10-01T00:00:00+00:00",
            "identifiers": [],
            "in_library": False,
        },
        "distance": 0,
        "in_degree": 0,
        "out_degree": 1,
        "pagerank": 0.5,
    }
    assert value["edges"] == [
        {"citing_paper_id": str(citing), "cited_paper_id": str(cited)}
    ]
    json.dumps(value)
