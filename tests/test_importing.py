"""Tests for the Core import/search use cases. No network access."""

import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from pdf_factory import write_pdf
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.documents import DocumentRecord, list_documents
from litlattice.errors import DocumentNotFound, InvalidIdentifier, WorkNotFound
from litlattice.importing import (
    CandidateStatus,
    ImportStatus,
    import_paper,
    import_paper_for_document,
    search_works,
)
from litlattice.models import (
    Citation,
    Paper,
    PaperIdentifier,
)
from litlattice.papers import add_to_library, create_paper, get_paper, list_papers
from litlattice.providers import ProviderWork
from litlattice.scan import scan_sources
from litlattice.sources import add_source


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
    """A paper provider over a fixed list of works."""

    def __init__(self, works: Sequence[ProviderWork]) -> None:
        self.works = list(works)
        self.lookups: list[list[tuple[str, str]]] = []

    def lookup_work(self, identifiers) -> ProviderWork | None:
        self.lookups.append(list(identifiers))
        wanted = {(s, v.lower()) for s, v in identifiers}
        for candidate in self.works:
            if {(s, v.lower()) for s, v in candidate.identifiers} & wanted:
                return candidate
        return None


class FixedProvider(FakeProvider):
    """Always answers with its first work, however the lookup was made."""

    def lookup_work(self, identifiers) -> ProviderWork | None:
        return self.works[0]


class FakeSearchProvider:
    def __init__(self, works: Sequence[ProviderWork]) -> None:
        self.works = list(works)
        self.calls: list[tuple[str, int]] = []

    def search_works(self, query: str, *, limit: int) -> list[ProviderWork]:
        self.calls.append((query, limit))
        return self.works[:limit]


def _keys(identifiers) -> set[tuple[str, str]]:
    return {(i.scheme, i.normalized_value) for i in identifiers}


def _counts(engine: Engine) -> tuple[int, int, int, int]:
    with Session(engine) as session:
        return (
            len(session.scalars(select(Paper)).all()),
            len(session.scalars(select(PaperIdentifier)).all()),
            session.scalar(
                select(func.count())
                .select_from(Paper)
                .where(Paper.library_added_at.is_not(None))
            ),
            len(session.scalars(select(Citation)).all()),
        )


def _listen_for_begins(engine: Engine) -> list[object]:
    begins: list[object] = []
    event.listen(engine, "begin", lambda connection: begins.append(connection))
    return begins


def test_import_by_doi_creates_paper(engine: Engine) -> None:
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = import_paper(engine, provider, [("doi", "10.1000/abc")])

    assert result.status is ImportStatus.created
    assert result.paper_id is not None
    assert result.title == "Attention"
    assert result.publication_year == 2017
    assert provider.lookups == [[("doi", "10.1000/abc")]]
    paper = get_paper(engine, result.paper_id)
    assert paper.title == "Attention"
    assert paper.publication_year == 2017
    assert not paper.in_library
    assert _keys(paper.identifiers) == {("doi", "10.1000/abc"), ("openalex", "W1")}
    assert _keys(result.identifiers_added) == {
        ("doi", "10.1000/abc"),
        ("openalex", "W1"),
    }


def test_import_by_arxiv_creates_paper(engine: Engine) -> None:
    provider = FakeProvider(
        [
            ProviderWork(
                (
                    ("openalex", "W2"),
                    ("doi", "10.48550/arxiv.2401.12345"),
                    ("arxiv", "2401.12345"),
                ),
                "A preprint",
                2024,
            )
        ]
    )

    result = import_paper(engine, provider, [("arxiv", "arXiv:2401.12345v2")])

    assert result.status is ImportStatus.created
    assert result.paper_id is not None
    paper = get_paper(engine, result.paper_id)
    assert _keys(paper.identifiers) == {
        ("arxiv", "2401.12345"),
        ("doi", "10.48550/arxiv.2401.12345"),
        ("openalex", "W2"),
    }


def test_importing_twice_matches_instead_of_duplicating(engine: Engine) -> None:
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    first = import_paper(engine, provider, [("doi", "10.1000/abc")])
    second = import_paper(engine, provider, [("doi", "10.1000/abc")])

    assert first.status is ImportStatus.created
    assert second.status is ImportStatus.matched
    assert second.paper_id == first.paper_id
    assert second.identifiers_added == ()
    assert len(list_papers(engine)) == 1


def test_import_matches_existing_paper_and_fills_missing_metadata(
    engine: Engine,
) -> None:
    existing = create_paper(engine, [("doi", "10.1000/abc")])
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = import_paper(engine, provider, [("doi", "10.1000/abc")])

    assert result.status is ImportStatus.matched
    assert result.paper_id == existing.id
    paper = get_paper(engine, existing.id)
    assert paper.title == "Attention"
    assert paper.publication_year == 2017
    assert ("openalex", "W1") in _keys(paper.identifiers)
    assert _keys(result.identifiers_added) == {("openalex", "W1")}


def test_import_never_overwrites_existing_metadata(engine: Engine) -> None:
    existing = create_paper(engine, [("doi", "10.1000/abc")], title="Mine")
    with Session(engine) as session, session.begin():
        session.get(Paper, existing.id).publication_year = 1999
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = import_paper(engine, provider, [("doi", "10.1000/abc")])

    assert result.status is ImportStatus.matched
    paper = get_paper(engine, existing.id)
    assert paper.title == "Mine"
    assert paper.publication_year == 1999


def test_import_unconfirmed_changes_nothing(engine: Engine) -> None:
    # The provider answers with a work reporting none of the input
    # identifiers, so it cannot be confirmed to be the same paper.
    provider = FixedProvider([work("W9", "10.1000/other", "Unrelated", 2021)])
    begins = _listen_for_begins(engine)

    result = import_paper(engine, provider, [("arxiv", "2401.12345")])

    assert result.status is ImportStatus.unconfirmed
    assert result.paper_id is None
    assert result.identifiers_added == ()
    assert _keys(result.identifiers) == {("arxiv", "2401.12345")}
    assert begins == []
    assert list_papers(engine) == []


def test_import_unknown_work_raises_and_changes_nothing(engine: Engine) -> None:
    provider = FakeProvider([])

    with pytest.raises(WorkNotFound) as excinfo:
        import_paper(engine, provider, [("doi", "10.1000/abc")])

    assert "doi:10.1000/abc" in str(excinfo.value)
    assert list_papers(engine) == []
    assert _counts(engine) == (0, 0, 0, 0)


def test_import_ambiguous_identifiers_change_nothing(engine: Engine) -> None:
    by_doi = create_paper(engine, [("doi", "10.1000/abc")])
    by_openalex = create_paper(engine, [("openalex", "W1")])
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = import_paper(engine, provider, [("doi", "10.1000/abc")])

    assert result.status is ImportStatus.ambiguous
    assert result.paper_id is None
    assert result.identifiers_added == ()
    assert result.candidate_paper_ids == tuple(sorted((by_doi.id, by_openalex.id)))
    assert len(get_paper(engine, by_doi.id).identifiers) == 1
    assert len(get_paper(engine, by_openalex.id).identifiers) == 1


def test_import_requires_an_identifier(engine: Engine) -> None:
    provider = FakeProvider([])

    with pytest.raises(ValueError):
        import_paper(engine, provider, [])

    assert provider.lookups == []


def test_import_rejects_invalid_identifiers(engine: Engine) -> None:
    provider = FakeProvider([])

    with pytest.raises(InvalidIdentifier):
        import_paper(engine, provider, [("doi", "not-a-doi")])

    assert provider.lookups == []


def test_import_never_touches_library_or_citations(engine: Engine) -> None:
    seeded = create_paper(engine, [("doi", "10.1000/seed")], title="Seed")
    add_to_library(engine, seeded.id)
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    import_paper(engine, provider, [("doi", "10.1000/abc")])

    assert [p.id for p in list_papers(engine) if p.in_library] == [seeded.id]
    with Session(engine) as session:
        assert session.scalars(select(Citation)).all() == []


def test_provider_lookup_happens_outside_the_transaction(engine: Engine) -> None:
    begins = _listen_for_begins(engine)

    class SpyProvider(FakeProvider):
        def lookup_work(self, identifiers) -> ProviderWork | None:
            assert begins == []
            return super().lookup_work(identifiers)

    provider = SpyProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = import_paper(engine, provider, [("doi", "10.1000/abc")])

    assert result.status is ImportStatus.created
    assert len(begins) == 1


def test_search_classifies_candidates_in_provider_order(engine: Engine) -> None:
    existing = create_paper(engine, [("doi", "10.1000/existing")], title="Existing")
    ambiguous_a = create_paper(engine, [("openalex", "W20")])
    ambiguous_b = create_paper(engine, [("doi", "10.1000/amb")])
    provider = FakeSearchProvider(
        [
            work("W10", "10.1000/new", "New one", 2020),
            work("W1", "10.1000/existing", "Existing", 2019),
            ProviderWork(
                (("openalex", "W20"), ("doi", "10.1000/amb")),
                "Ambiguous",
                2018,
            ),
        ]
    )

    result = search_works(engine, provider, "attention", limit=3)

    assert result.query == "attention"
    assert provider.calls == [("attention", 3)]
    assert [c.title for c in result.candidates] == [
        "New one",
        "Existing",
        "Ambiguous",
    ]
    assert [c.status for c in result.candidates] == [
        CandidateStatus.new,
        CandidateStatus.existing,
        CandidateStatus.ambiguous,
    ]
    assert result.candidates[0].paper_id is None
    assert result.candidates[1].paper_id == existing.id
    assert result.candidates[2].paper_id is None
    assert result.candidates[2].candidate_paper_ids == tuple(
        sorted((ambiguous_a.id, ambiguous_b.id))
    )


def test_search_does_not_identify_by_title(engine: Engine) -> None:
    create_paper(engine, [("doi", "10.1000/other")], title="Attention")
    provider = FakeSearchProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = search_works(engine, provider, "Attention")

    assert len(result.candidates) == 1
    assert result.candidates[0].status is CandidateStatus.new
    assert result.candidates[0].paper_id is None


def test_search_drops_works_without_usable_identifiers(engine: Engine) -> None:
    provider = FakeSearchProvider(
        [
            ProviderWork((("doi", "not-a-doi"),), "Broken"),
            ProviderWork((), "No identifiers"),
            work("W1", "10.1000/abc", "Good", 2017),
        ]
    )

    result = search_works(engine, provider, "attention")

    assert [c.title for c in result.candidates] == ["Good"]


def test_search_strips_the_query(engine: Engine) -> None:
    provider = FakeSearchProvider([])

    result = search_works(engine, provider, "  attention  ")

    assert provider.calls == [("attention", 10)]
    assert result.query == "attention"


def test_search_rejects_empty_query_and_bad_limit(engine: Engine) -> None:
    provider = FakeSearchProvider([work("W1", "10.1000/abc")])

    for query in ("", "   "):
        with pytest.raises(ValueError):
            search_works(engine, provider, query)
    for limit in (-1, 0, 51):
        with pytest.raises(ValueError):
            search_works(engine, provider, "attention", limit=limit)

    assert provider.calls == []


def test_search_does_not_change_the_database(engine: Engine) -> None:
    create_paper(engine, [("doi", "10.1000/existing")], title="Existing")
    before = _counts(engine)
    provider = FakeSearchProvider(
        [
            work("W10", "10.1000/new", "New one", 2020),
            work("W1", "10.1000/existing", "Existing", 2019),
        ]
    )

    search_works(engine, provider, "attention")

    assert _counts(engine) == before
    assert len(list_papers(engine)) == 1


def test_search_provider_happens_outside_the_transaction(engine: Engine) -> None:
    begins = _listen_for_begins(engine)

    class SpyProvider(FakeSearchProvider):
        def search_works(self, query: str, *, limit: int) -> list[ProviderWork]:
            assert begins == []
            return super().search_works(query, limit=limit)

    provider = SpyProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = search_works(engine, provider, "attention")

    assert len(result.candidates) == 1
    assert len(begins) == 1


def test_input_identifiers_the_work_does_not_report_are_not_attached(
    engine: Engine,
) -> None:
    # A published DOI together with an arXiv ID may be a preprint and its
    # published version: only what the work itself reports is attached.
    provider = FakeProvider([work("W1", "10.1000/published", "Published", 2021)])

    result = import_paper(
        engine, provider, [("doi", "10.1000/published"), ("arxiv", "2401.12345")]
    )

    assert result.status is ImportStatus.created
    assert _keys(result.identifiers_ignored) == {("arxiv", "2401.12345")}
    paper = get_paper(engine, result.paper_id)
    assert _keys(paper.identifiers) == {
        ("doi", "10.1000/published"),
        ("openalex", "W1"),
    }


def _scanned_document(engine: Engine, tmp_path: Path) -> DocumentRecord:
    """A DocumentCopy with no hints, scanned but left unidentified."""
    write_pdf(tmp_path / "paper.pdf")
    add_source(engine, tmp_path)
    scan_sources(engine)
    (document,) = list_documents(engine)
    return document


def test_import_for_document_creates_paper_and_links(
    engine: Engine, tmp_path: Path
) -> None:
    document = _scanned_document(engine, tmp_path)
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = import_paper_for_document(
        engine, provider, document.id, [("doi", "10.1000/abc")]
    )

    assert result.document_id == document.id
    assert result.import_result.status is ImportStatus.created
    assert result.linked
    paper = get_paper(engine, result.import_result.paper_id)
    assert not paper.in_library
    (linked_document,) = list_documents(engine, paper_id=paper.id)
    assert linked_document.id == document.id


def test_import_for_document_twice_matches_without_relinking(
    engine: Engine, tmp_path: Path
) -> None:
    document = _scanned_document(engine, tmp_path)
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    first = import_paper_for_document(
        engine, provider, document.id, [("doi", "10.1000/abc")]
    )
    second = import_paper_for_document(
        engine, provider, document.id, [("doi", "10.1000/abc")]
    )

    assert first.import_result.status is ImportStatus.created
    assert first.linked
    assert second.import_result.status is ImportStatus.matched
    assert second.import_result.paper_id == first.import_result.paper_id
    assert not second.linked
    assert len(list_papers(engine)) == 1


def test_import_for_document_unconfirmed_links_nothing(
    engine: Engine, tmp_path: Path
) -> None:
    document = _scanned_document(engine, tmp_path)
    provider = FixedProvider([work("W9", "10.1000/other", "Unrelated", 2021)])

    result = import_paper_for_document(
        engine, provider, document.id, [("arxiv", "2401.12345")]
    )

    assert result.import_result.status is ImportStatus.unconfirmed
    assert not result.linked
    assert list_papers(engine) == []
    assert list_documents(engine)[0].paper_id is None


def test_import_for_document_ambiguous_links_nothing(
    engine: Engine, tmp_path: Path
) -> None:
    by_doi = create_paper(engine, [("doi", "10.1000/abc")])
    by_openalex = create_paper(engine, [("openalex", "W1")])
    document = _scanned_document(engine, tmp_path)
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    result = import_paper_for_document(
        engine, provider, document.id, [("doi", "10.1000/abc")]
    )

    assert result.import_result.status is ImportStatus.ambiguous
    assert not result.linked
    assert list_documents(engine, paper_id=by_doi.id) == []
    assert list_documents(engine, paper_id=by_openalex.id) == []


def test_import_for_document_unknown_document_never_calls_provider(
    engine: Engine,
) -> None:
    provider = FakeProvider([work("W1", "10.1000/abc", "Attention", 2017)])

    with pytest.raises(DocumentNotFound):
        import_paper_for_document(
            engine, provider, uuid.uuid4(), [("doi", "10.1000/abc")]
        )

    assert provider.lookups == []
    assert list_papers(engine) == []


def test_import_for_document_unknown_work_raises_and_links_nothing(
    engine: Engine, tmp_path: Path
) -> None:
    document = _scanned_document(engine, tmp_path)
    provider = FakeProvider([])

    with pytest.raises(WorkNotFound):
        import_paper_for_document(
            engine, provider, document.id, [("doi", "10.1000/abc")]
        )

    assert list_papers(engine) == []
    assert list_documents(engine)[0].paper_id is None
