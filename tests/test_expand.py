import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.citations import list_all_citations, list_citations, list_references
from litlattice.errors import PaperNotIdentifiable, ProviderWorkNotFound
from litlattice.expand import (
    ExpandDirection,
    MetadataStatus,
    expand_citations,
    fetch_paper_metadata,
)
from litlattice.identity import ResolutionStatus
from litlattice.models import (
    Citation,
    DocumentCopy,
    PaperIdentifier,
)
from litlattice.papers import add_to_library, create_paper, get_paper, list_papers
from litlattice.providers import ProviderWork


def work(openalex: str, doi: str | None = None, title: str | None = None, year=None):
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
    # S cites R1, R2; C1 and C2 cite S; R1 cites R3.
    return FakeProvider(
        [
            work("W1", "10.1/s", "Seed", 2020),
            work("W2", "10.1/r1", "Ref one", 2019),
            work("W3", "10.1/r2", "Ref two", 2018),
            work("W4", None, "Citer one", 2021),
            work("W5", "10.1/c2", "Citer two", 2022),
            work("W6", "10.1/r3", "Ref three", 2010),
        ],
        {"W1": ["W2", "W3"], "W4": ["W1"], "W5": ["W1"], "W2": ["W6"]},
    )


def _titles(records) -> set[str]:
    return {r.title for r in records}


def test_references_are_stored_as_seed_citing_them(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [("doi", "10.1/S")])

    result = expand_citations(
        engine, graph, seed.id, direction=ExpandDirection.references
    )

    assert _titles(list_references(engine, seed.id)) == {"Ref one", "Ref two"}
    assert list_citations(engine, seed.id) == []
    assert result.citations_created == 2
    assert len(result.papers_created) == 2


def test_citations_are_stored_as_citers_citing_seed(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])

    expand_citations(engine, graph, seed.id, direction=ExpandDirection.citations)

    assert _titles(list_citations(engine, seed.id)) == {"Citer one", "Citer two"}
    assert list_references(engine, seed.id) == []


def test_both_directions_and_seed_metadata(engine: Engine, graph: FakeProvider) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])

    result = expand_citations(engine, graph, seed.id)

    assert len(list_all_citations(engine)) == 4
    paper = get_paper(engine, seed.id)
    assert paper.title == "Seed"
    assert paper.publication_year == 2020
    assert ("openalex", "W1") in {
        (i.scheme, i.normalized_value) for i in paper.identifiers
    }
    assert result.seed_metadata.status is MetadataStatus.updated


def test_discovered_papers_have_identifiers_and_metadata(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])

    expand_citations(engine, graph, seed.id, direction=ExpandDirection.references)

    ref = next(p for p in list_papers(engine) if p.title == "Ref one")
    assert ref.publication_year == 2019
    assert {(i.scheme, i.normalized_value) for i in ref.identifiers} == {
        ("openalex", "W2"),
        ("doi", "10.1/r1"),
    }


def test_expand_never_touches_library_or_documents(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])
    add_to_library(engine, seed.id)

    expand_citations(engine, graph, seed.id, depth=2)

    with Session(engine) as session:
        assert session.scalars(select(DocumentCopy)).all() == []
    assert [p.id for p in list_papers(engine) if p.in_library] == [seed.id]


def test_expand_is_idempotent(engine: Engine, graph: FakeProvider) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])

    expand_citations(engine, graph, seed.id)
    papers_before = len(list_papers(engine))
    again = expand_citations(engine, graph, seed.id)

    assert len(list_papers(engine)) == papers_before
    assert again.papers_created == ()
    assert again.citations_created == 0
    assert again.citations_confirmed == 4


def test_existing_papers_are_reused_by_exact_identifier(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])
    mine = create_paper(engine, [("doi", "10.1/R1")], title="My title")

    result = expand_citations(
        engine, graph, seed.id, direction=ExpandDirection.references
    )

    assert mine.id in result.papers_matched
    assert get_paper(engine, mine.id).title == "My title"
    assert mine.id in {r.id for r in list_references(engine, seed.id)}


def test_same_title_does_not_merge(engine: Engine, graph: FakeProvider) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])
    lookalike = create_paper(engine, [("doi", "10.9/other")], title="Ref one")

    expand_citations(engine, graph, seed.id, direction=ExpandDirection.references)

    refs = list_references(engine, seed.id)
    assert lookalike.id not in {r.id for r in refs}
    assert [p.title for p in list_papers(engine)].count("Ref one") == 2


def test_work_matching_two_papers_is_skipped(engine: Engine) -> None:
    provider = FakeProvider(
        [work("W1", "10.1/s"), work("W2", "10.1/r1")], {"W1": ["W2"]}
    )
    seed = create_paper(engine, [("doi", "10.1/s")])
    create_paper(engine, [("doi", "10.1/r1")])
    create_paper(engine, [("openalex", "W2")])

    result = expand_citations(engine, provider, seed.id)

    assert len(result.skipped) == 1
    assert result.skipped[0].status is ResolutionStatus.ambiguous
    assert len(result.skipped[0].candidate_paper_ids) == 2
    assert list_references(engine, seed.id) == []


def test_depth_two_follows_discovered_works(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])

    expand_citations(
        engine, graph, seed.id, direction=ExpandDirection.references, depth=2
    )

    ref_one = next(p for p in list_papers(engine) if p.title == "Ref one")
    assert _titles(list_references(engine, ref_one.id)) == {"Ref three"}


def test_max_nodes_limits_and_reports_truncation(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])

    result = expand_citations(engine, graph, seed.id, depth=2, max_nodes=3)

    assert len(list_papers(engine)) == 3
    assert result.truncated


def test_seed_without_identifier_is_rejected(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [], title="Untitled")

    with pytest.raises(PaperNotIdentifiable):
        expand_citations(engine, graph, seed.id)


def test_seed_unknown_to_provider_is_rejected(
    engine: Engine, graph: FakeProvider
) -> None:
    seed = create_paper(engine, [("doi", "10.1/unknown")])

    with pytest.raises(ProviderWorkNotFound):
        expand_citations(engine, graph, seed.id)
    with Session(engine) as session:
        assert session.scalars(select(Citation)).all() == []


def test_nothing_applied_without_shared_identifier(engine: Engine) -> None:
    # OpenAlex answered an arXiv lookup with a record carrying another work's
    # DOI and title; it must not be applied to the Paper.
    corrupted = ProviderWork(
        (("openalex", "W1"), ("doi", "10.1/unrelated")), "Unrelated title", 2021
    )

    class CorruptedRecord(FakeProvider):
        def lookup_work(self, identifiers):
            return corrupted

    provider = CorruptedRecord([corrupted], {})
    preprint = create_paper(engine, [("arxiv", "2401.12345")])

    result = fetch_paper_metadata(engine, provider, preprint.id)

    assert result.status is MetadataStatus.unconfirmed
    assert result.identifiers_added == ()
    paper = get_paper(engine, preprint.id)
    assert paper.title is None
    assert paper.publication_year is None
    assert [i.scheme for i in paper.identifiers] == ["arxiv"]


def _identifier_count(engine: Engine) -> int:
    with Session(engine) as session:
        return len(session.scalars(select(PaperIdentifier)).all())


def test_unconfirmed_seed_stores_nothing(engine: Engine) -> None:
    # OpenAlex answered an arXiv lookup with an unrelated work; its
    # references must not become the Paper's citations.
    unrelated = work("W9", "10.1/unrelated", "Unrelated", 2021)
    cited = work("W2", "10.1/cited", "Cited", 2019)

    class UnrelatedRecord(FakeProvider):
        def lookup_work(self, identifiers):
            return unrelated

    provider = UnrelatedRecord([unrelated, cited], {"W9": ["W2"]})
    preprint = create_paper(engine, [("arxiv", "2401.12345")])
    identifiers_before = _identifier_count(engine)

    result = expand_citations(engine, provider, preprint.id)

    assert result.seed_metadata.status is MetadataStatus.unconfirmed
    assert result.papers_created == ()
    assert result.papers_matched == ()
    assert result.citations_created == 0
    assert result.citations_confirmed == 0
    assert result.skipped == ()
    assert result.truncated is False
    assert provider.calls == []
    assert len(list_papers(engine)) == 1
    assert list_all_citations(engine) == []
    assert _identifier_count(engine) == identifiers_before


def test_conflicting_seed_stores_nothing(engine: Engine) -> None:
    provider = FakeProvider(
        [work("W1", "10.1/s", "Seed", 2020), work("W2", "10.1/r1", "Ref one", 2019)],
        {"W1": ["W2"]},
    )
    seed = create_paper(engine, [("doi", "10.1/s")])
    create_paper(engine, [("openalex", "W1")])
    identifiers_before = _identifier_count(engine)

    result = expand_citations(
        engine, provider, seed.id, direction=ExpandDirection.references
    )

    assert result.seed_metadata.status is MetadataStatus.conflict
    assert result.papers_created == ()
    assert result.papers_matched == ()
    assert result.citations_created == 0
    assert result.citations_confirmed == 0
    assert list_references(engine, seed.id) == []
    assert len(list_papers(engine)) == 2
    assert _identifier_count(engine) == identifiers_before


def test_fetch_metadata_fills_but_never_overwrites(
    engine: Engine, graph: FakeProvider
) -> None:
    paper = create_paper(engine, [("doi", "10.1/s")], title="Mine")

    result = fetch_paper_metadata(engine, graph, paper.id)

    assert result.status is MetadataStatus.updated
    assert not result.title_set
    assert result.publication_year_set
    loaded = get_paper(engine, paper.id)
    assert loaded.title == "Mine"
    assert loaded.publication_year == 2020
    assert not loaded.in_library


def test_fetch_metadata_conflict_changes_nothing(
    engine: Engine, graph: FakeProvider
) -> None:
    paper = create_paper(engine, [("doi", "10.1/s")])
    other = create_paper(engine, [("openalex", "W1")])

    result = fetch_paper_metadata(engine, graph, paper.id)

    assert result.status is MetadataStatus.conflict
    assert get_paper(engine, paper.id).title is None
    assert len(get_paper(engine, other.id).identifiers) == 1


def test_invalid_expand_arguments(engine: Engine, graph: FakeProvider) -> None:
    seed = create_paper(engine, [("doi", "10.1/s")])
    with pytest.raises(ValueError):
        expand_citations(engine, graph, seed.id, depth=0)
    with pytest.raises(ValueError):
        expand_citations(engine, graph, seed.id, max_nodes=0)


def test_unknown_seed_paper(engine: Engine, graph: FakeProvider) -> None:
    from litlattice.errors import PaperNotFound

    with pytest.raises(PaperNotFound):
        expand_citations(engine, graph, uuid.uuid4())


def test_both_directions_share_the_node_budget(engine: Engine) -> None:
    refs = [work(f"W{n}", f"10.1/r{n}") for n in range(10, 20)]
    citers = [work(f"W{n}", f"10.1/c{n}") for n in range(20, 30)]
    cites = {"W1": [w.identifiers[0][1] for w in refs]}
    cites.update({w.identifiers[0][1]: ["W1"] for w in citers})
    provider = FakeProvider([work("W1", "10.1/s"), *refs, *citers], cites)
    seed = create_paper(engine, [("doi", "10.1/s")])

    result = expand_citations(engine, provider, seed.id, max_nodes=7)

    assert result.truncated
    assert len(list_references(engine, seed.id)) == 3
    assert len(list_citations(engine, seed.id)) == 3
