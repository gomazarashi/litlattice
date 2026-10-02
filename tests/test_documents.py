import uuid
from pathlib import Path

import pytest
from pdf_factory import write_pdf
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.documents import (
    IdentificationStatus,
    identify_documents,
    inspect_document,
    link_document,
    list_documents,
    unlink_document,
)
from litlattice.errors import DocumentNotFound, PaperNotFound
from litlattice.models import DocumentCopy, Paper, PaperIdentifier
from litlattice.papers import create_paper, get_paper, list_papers
from litlattice.pdf_identifiers import HintOrigin
from litlattice.scan import scan_sources
from litlattice.sources import add_source


def _scan(engine: Engine, root: Path):
    add_source(engine, root)
    return scan_sources(engine)


def _statuses(result) -> dict[str, IdentificationStatus]:
    return {Path(i.path).name: i.status for i in result.identifications}


def test_scan_creates_paper_from_arxiv_filename(engine: Engine, tmp_path: Path) -> None:
    write_pdf(tmp_path / "2401.12345v2.pdf")

    result = _scan(engine, tmp_path)

    (identification,) = result.identifications
    assert identification.status is IdentificationStatus.created
    paper = get_paper(engine, identification.paper_id)
    assert [(i.scheme, i.normalized_value) for i in paper.identifiers] == [
        ("arxiv", "2401.12345")
    ]
    assert not paper.in_library
    (document,) = list_documents(engine, paper_id=paper.id)
    assert document.path == str(tmp_path / "2401.12345v2.pdf")


def test_scan_links_existing_paper_by_metadata_doi(
    engine: Engine, tmp_path: Path
) -> None:
    paper = create_paper(engine, [("doi", "10.1000/xyz")], title="Known")
    write_pdf(tmp_path / "paper.pdf", metadata={"/doi": "10.1000/XYZ"})

    result = _scan(engine, tmp_path)

    assert _statuses(result) == {"paper.pdf": IdentificationStatus.linked}
    assert result.identifications[0].paper_id == paper.id
    assert len(list_papers(engine)) == 1


def test_scan_leaves_unidentifiable_document_unlinked(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "Some Paper.pdf", pages_text=["No identifiers here"])

    result = _scan(engine, tmp_path)

    assert _statuses(result) == {"Some Paper.pdf": IdentificationStatus.unidentified}
    assert list_papers(engine) == []
    (document,) = list_documents(engine, unlinked_only=True)
    assert document.paper_id is None


def test_preprint_and_published_hints_are_not_merged(
    engine: Engine, tmp_path: Path
) -> None:
    # arXiv file name plus a publisher DOI in metadata: possibly two Papers.
    write_pdf(tmp_path / "2401.12345.pdf", metadata={"/doi": "10.1000/published"})

    result = _scan(engine, tmp_path)

    (identification,) = result.identifications
    assert identification.status is IdentificationStatus.ambiguous
    assert {i.scheme for i in identification.identifiers} == {"arxiv", "doi"}
    assert list_papers(engine) == []


def test_hints_for_two_papers_are_ambiguous(engine: Engine, tmp_path: Path) -> None:
    preprint = create_paper(engine, [("arxiv", "2401.12345")])
    published = create_paper(engine, [("doi", "10.1000/published")])
    write_pdf(tmp_path / "2401.12345.pdf", metadata={"/doi": "10.1000/published"})

    result = _scan(engine, tmp_path)

    (identification,) = result.identifications
    assert identification.status is IdentificationStatus.ambiguous
    assert set(identification.candidate_paper_ids) == {preprint.id, published.id}


def test_agreeing_hints_of_one_paper_link(engine: Engine, tmp_path: Path) -> None:
    paper = create_paper(engine, [("arxiv", "2401.12345"), ("doi", "10.1/x")])
    write_pdf(tmp_path / "2401.12345.pdf", metadata={"/doi": "10.1/x"})

    result = _scan(engine, tmp_path)

    assert result.identifications[0].status is IdentificationStatus.linked
    assert result.identifications[0].paper_id == paper.id


def test_single_text_doi_is_used_without_strong_hints(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "a.pdf", pages_text=["Journal X. https://doi.org/10.5555/abc"])
    write_pdf(
        tmp_path / "b.pdf",
        pages_text=["See doi:10.5555/one and doi:10.5555/two for details"],
    )

    result = _scan(engine, tmp_path)

    assert _statuses(result) == {
        "a.pdf": IdentificationStatus.created,
        "b.pdf": IdentificationStatus.ambiguous,
    }


def test_rescan_does_not_duplicate_papers_or_links(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "2401.12345.pdf")
    _scan(engine, tmp_path)

    again = scan_sources(engine)

    assert again.identifications == ()
    assert len(list_papers(engine)) == 1
    assert list_documents(engine)[0].paper_id is not None


def test_scan_identification_never_adds_to_library(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "2401.12345.pdf")
    _scan(engine, tmp_path)

    assert all(not paper.in_library for paper in list_papers(engine))
    with Session(engine) as session:
        assert all(
            paper.library_added_at is None
            for paper in session.scalars(select(Paper)).all()
        )


def test_identify_documents_picks_up_papers_added_later(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "a.pdf", pages_text=["doi:10.5555/one and doi:10.5555/two"])
    _scan(engine, tmp_path)
    paper = create_paper(engine, [("doi", "10.5555/one"), ("doi", "10.5555/two")])

    (identification,) = identify_documents(engine)

    assert identification.status is IdentificationStatus.linked
    assert identification.paper_id == paper.id


def test_identify_documents_skips_missing_and_unknown(
    engine: Engine, tmp_path: Path
) -> None:
    pdf = write_pdf(tmp_path / "2401.12345.pdf")
    _scan(engine, tmp_path)
    # Unlink the identified file, then let it go missing.
    (document,) = list_documents(engine)
    unlink_document(engine, document.id, document.paper_id)
    pdf.unlink()
    scan_sources(engine)

    assert identify_documents(engine) == []
    with pytest.raises(DocumentNotFound):
        identify_documents(engine, document_ids=[uuid.uuid4()])


def test_manual_link_and_unlink_keep_both_records(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "unknown.pdf")
    _scan(engine, tmp_path)
    paper = create_paper(engine, [], title="Chosen by hand")
    (document,) = list_documents(engine)

    assert link_document(engine, document.id, paper.id)
    assert not link_document(engine, document.id, paper.id)
    assert list_documents(engine, paper_id=paper.id)[0].id == document.id

    assert unlink_document(engine, document.id, paper.id)
    assert not unlink_document(engine, document.id, paper.id)
    assert get_paper(engine, paper.id).title == "Chosen by hand"
    assert list_documents(engine)[0].paper_id is None
    assert (tmp_path / "unknown.pdf").exists()


def test_link_replaces_the_previous_paper(engine: Engine, tmp_path: Path) -> None:
    write_pdf(tmp_path / "unknown.pdf")
    _scan(engine, tmp_path)
    first = create_paper(engine, [], title="First")
    second = create_paper(engine, [], title="Second")
    (document,) = list_documents(engine)

    assert link_document(engine, document.id, first.id)
    assert link_document(engine, document.id, second.id)

    assert list_documents(engine, paper_id=first.id) == []
    assert [d.id for d in list_documents(engine, paper_id=second.id)] == [document.id]
    assert list_documents(engine)[0].paper_id == second.id


def test_unlink_wrong_paper_does_not_clear_the_link(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "unknown.pdf")
    _scan(engine, tmp_path)
    linked = create_paper(engine, [])
    other = create_paper(engine, [])
    (document,) = list_documents(engine)
    link_document(engine, document.id, linked.id)

    assert unlink_document(engine, document.id, other.id) is False
    assert list_documents(engine)[0].paper_id == linked.id


def test_link_errors(engine: Engine, tmp_path: Path) -> None:
    write_pdf(tmp_path / "unknown.pdf")
    _scan(engine, tmp_path)
    paper = create_paper(engine, [])
    (document,) = list_documents(engine)

    with pytest.raises(DocumentNotFound):
        link_document(engine, uuid.uuid4(), paper.id)
    with pytest.raises(PaperNotFound):
        link_document(engine, document.id, uuid.uuid4())
    with pytest.raises(PaperNotFound):
        list_documents(engine, paper_id=uuid.uuid4())


def test_list_documents_filters_unlinked(engine: Engine, tmp_path: Path) -> None:
    write_pdf(tmp_path / "2401.12345.pdf")
    write_pdf(tmp_path / "Some Paper.pdf", pages_text=["No identifiers here"])
    _scan(engine, tmp_path)

    linked = list_documents(engine, unlinked_only=False)
    unlinked = list_documents(engine, unlinked_only=True)

    assert len(linked) == 2
    (document,) = unlinked
    assert Path(document.path).name == "Some Paper.pdf"


def _counts(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as session:
        return (
            len(session.scalars(select(Paper)).all()),
            len(session.scalars(select(PaperIdentifier)).all()),
            len(session.scalars(select(DocumentCopy)).all()),
        )


def test_inspect_reports_preprint_and_published_hints(
    engine: Engine, tmp_path: Path
) -> None:
    # arXiv file name plus a publisher DOI in metadata: possibly two Papers.
    write_pdf(tmp_path / "2401.12345.pdf", metadata={"/doi": "10.1000/published"})
    _scan(engine, tmp_path)
    (document,) = list_documents(engine)

    inspection = inspect_document(engine, document.id)

    assert inspection.document.id == document.id
    assert inspection.file_available
    assert [(h.scheme, h.normalized_value) for h in inspection.hints] == [
        ("arxiv", "2401.12345"),
        ("doi", "10.1000/published"),
    ]
    assert all(h.strong for h in inspection.hints)
    assert all(h.paper_id is None for h in inspection.hints)
    assert inspection.assessment is IdentificationStatus.ambiguous
    assert inspection.candidates == ()


def test_inspect_reports_candidate_papers_per_hint(
    engine: Engine, tmp_path: Path
) -> None:
    preprint = create_paper(engine, [("arxiv", "2401.12345")])
    published = create_paper(engine, [("doi", "10.1000/published")])
    write_pdf(tmp_path / "2401.12345.pdf", metadata={"/doi": "10.1000/published"})
    _scan(engine, tmp_path)
    (document,) = list_documents(engine)

    inspection = inspect_document(engine, document.id)

    assert {h.scheme: h.paper_id for h in inspection.hints} == {
        "arxiv": preprint.id,
        "doi": published.id,
    }
    assert {c.id for c in inspection.candidates} == {preprint.id, published.id}
    assert inspection.assessment is IdentificationStatus.ambiguous


def test_inspect_marks_first_page_text_doi_as_weak(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "a.pdf", pages_text=["doi:10.5555/abc"])
    _scan(engine, tmp_path)
    (document,) = list_documents(engine)

    inspection = inspect_document(engine, document.id)

    (hint,) = inspection.hints
    assert hint.origin is HintOrigin.pdf_text
    assert not hint.strong


def test_inspect_without_identifiers_reports_unidentified(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "Some Paper.pdf", pages_text=["No identifiers here"])
    _scan(engine, tmp_path)
    (document,) = list_documents(engine)

    inspection = inspect_document(engine, document.id)

    assert inspection.file_available
    assert inspection.hints == ()
    assert inspection.assessment is IdentificationStatus.unidentified
    assert inspection.candidates == ()


def test_inspect_linked_document_has_no_assessment_and_keeps_candidates(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "2401.12345.pdf")
    _scan(engine, tmp_path)
    (document,) = list_documents(engine)

    inspection = inspect_document(engine, document.id)

    assert inspection.assessment is None
    assert [c.id for c in inspection.candidates] == [document.paper_id]


def test_inspect_reports_missing_file_before_scan_notices(
    engine: Engine, tmp_path: Path
) -> None:
    pdf = write_pdf(tmp_path / "2401.12345.pdf")
    _scan(engine, tmp_path)
    (document,) = list_documents(engine)
    pdf.unlink()

    inspection = inspect_document(engine, document.id)

    assert document.missing_since is None
    assert not inspection.file_available
    assert inspection.hints == ()
    assert inspection.assessment is None


def test_inspect_unknown_document(engine: Engine) -> None:
    with pytest.raises(DocumentNotFound):
        inspect_document(engine, uuid.uuid4())


def test_inspect_changes_nothing_in_the_database(
    engine: Engine, tmp_path: Path
) -> None:
    write_pdf(tmp_path / "2401.12345.pdf", metadata={"/doi": "10.1000/published"})
    _scan(engine, tmp_path)
    (document,) = list_documents(engine)
    before = _counts(engine), list_documents(engine)

    inspect_document(engine, document.id)

    assert (_counts(engine), list_documents(engine)) == before
