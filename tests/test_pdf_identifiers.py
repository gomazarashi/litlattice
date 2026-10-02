import logging
import warnings
from pathlib import Path

import pytest
from pdf_factory import write_pdf as _write_pdf

from litlattice.pdf_identifiers import (
    HintOrigin,
    IdentifierHint,
    _quiet_pypdf,
    _strip_trailing_punctuation,
    extract_identifier_hints,
)


def test_arxiv_filename_hint(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path / "2401.12345v2.pdf")
    assert extract_identifier_hints(path) == (
        IdentifierHint("arxiv", "2401.12345v2", "2401.12345", HintOrigin.filename),
    )


def test_arxiv_filename_extension_is_case_insensitive(tmp_path: Path) -> None:
    assert extract_identifier_hints(tmp_path / "2401.12345v10.PDF") == (
        IdentifierHint("arxiv", "2401.12345v10", "2401.12345", HintOrigin.filename),
    )


def test_non_arxiv_filenames_yield_no_hints(tmp_path: Path) -> None:
    for name in (
        "Attention Is All You Need.pdf",
        "paper-10.1000.pdf",
        "2401.123.pdf",
        "arXiv-2401.12345.pdf",
    ):
        assert extract_identifier_hints(_write_pdf(tmp_path / name)) == ()


def test_info_doi_hint(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path / "paper.pdf", metadata={"/doi": "10.1000/XYZ"})
    assert extract_identifier_hints(path) == (
        IdentifierHint("doi", "10.1000/XYZ", "10.1000/xyz", HintOrigin.pdf_metadata),
    )


def test_info_doi_key_is_case_insensitive(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "paper.pdf",
        metadata={"/DOI": "https://doi.org/10.1234/AbC"},
    )
    assert extract_identifier_hints(path) == (
        IdentifierHint(
            "doi",
            "https://doi.org/10.1234/AbC",
            "10.1234/abc",
            HintOrigin.pdf_metadata,
        ),
    )


def test_invalid_metadata_doi_is_ignored(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "paper.pdf",
        metadata={"/doi": "not-a-doi", "/title": "10.1000/not-the-doi"},
    )
    assert extract_identifier_hints(path) == ()


_XMP = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description
      xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
      xmlns:pdfx="http://ns.adobe.com/pdfx/1.3/"
      xmlns:crossmark="http://crossref.org/crossmark/1.0/"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      prism:doi="10.1000/attr.1"
      pdfx:doi="doi:10.1000/attr.2"
      crossmark:DOI="10.1000/attr.3"
      dc:identifier="doi:10.1000/attr.4">
   <prism:doi>10.1000/elem.1</prism:doi>
   <pdfx:doi>https://doi.org/10.1000/elem.2</pdfx:doi>
   <crossmark:DOI>
    <rdf:li>10.1000/elem.3</rdf:li>
   </crossmark:DOI>
   <dc:identifier>
    <rdf:Bag>
     <rdf:li>doi:10.1000/dc.1</rdf:li>
     <rdf:li>urn:isbn:978-0-00-000000-0</rdf:li>
     <rdf:li>just some text</rdf:li>
    </rdf:Bag>
   </dc:identifier>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def test_xmp_metadata_hints(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path / "xmp.pdf", xmp=_XMP)

    def hint(value: str, normalized: str) -> IdentifierHint:
        return IdentifierHint("doi", value, normalized, HintOrigin.pdf_metadata)

    assert extract_identifier_hints(path) == (
        hint("10.1000/attr.1", "10.1000/attr.1"),
        hint("doi:10.1000/attr.2", "10.1000/attr.2"),
        hint("10.1000/attr.3", "10.1000/attr.3"),
        hint("doi:10.1000/attr.4", "10.1000/attr.4"),
        hint("10.1000/elem.1", "10.1000/elem.1"),
        hint("https://doi.org/10.1000/elem.2", "10.1000/elem.2"),
        hint("10.1000/elem.3", "10.1000/elem.3"),
        hint("doi:10.1000/dc.1", "10.1000/dc.1"),
    )


def test_text_arxiv_stamp_and_doi(tmp_path: Path) -> None:
    text = "arXiv:2401.12345v2 [cs.LG] https://doi.org/10.1234/abc.5678."
    path = _write_pdf(tmp_path / "text.pdf", pages_text=(text,))
    assert extract_identifier_hints(path) == (
        IdentifierHint("arxiv", "2401.12345v2", "2401.12345", HintOrigin.pdf_text),
        IdentifierHint(
            "doi", "10.1234/abc.5678", "10.1234/abc.5678", HintOrigin.pdf_text
        ),
    )


def test_text_doi_trailing_punctuation_is_stripped(tmp_path: Path) -> None:
    text = (
        "10.1234/one.1, 10.1234/two.2; 10.1234/three.3: 10.1234/four.4] 10.1234/five.5}"
    )
    path = _write_pdf(tmp_path / "text.pdf", pages_text=(text,))
    assert [h.normalized_value for h in extract_identifier_hints(path)] == [
        "10.1234/one.1",
        "10.1234/two.2",
        "10.1234/three.3",
        "10.1234/four.4",
        "10.1234/five.5",
    ]


def test_invalid_text_doi_like_values_are_ignored(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "text.pdf",
        pages_text=("10.1/abc and 10.1234/ too",),
    )
    assert extract_identifier_hints(path) == ()


def test_second_page_is_not_read(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "two.pdf",
        pages_text=("first page without identifiers", "10.1000/second.only"),
    )
    assert extract_identifier_hints(path) == ()


def test_duplicate_text_hints_are_removed(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "dup.pdf",
        pages_text=("10.1234/dup.1 ... 10.1234/DUP.1",),
    )
    assert extract_identifier_hints(path) == (
        IdentifierHint("doi", "10.1234/dup.1", "10.1234/dup.1", HintOrigin.pdf_text),
    )


def test_same_identifier_from_different_origins_is_kept(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "both.pdf",
        pages_text=("10.1234/dup.9",),
        metadata={"/doi": "10.1234/dup.9"},
    )
    hints = extract_identifier_hints(path)
    assert [h.origin for h in hints] == [HintOrigin.pdf_metadata, HintOrigin.pdf_text]
    assert len(hints) == 2


def test_broken_pdf_returns_filename_hint_only(tmp_path: Path) -> None:
    path = tmp_path / "2401.12345v2.pdf"
    path.write_bytes(b"not a pdf")
    assert extract_identifier_hints(path) == (
        IdentifierHint("arxiv", "2401.12345v2", "2401.12345", HintOrigin.filename),
    )


def test_missing_file_does_not_raise(tmp_path: Path) -> None:
    assert extract_identifier_hints(tmp_path / "missing.pdf") == ()
    assert extract_identifier_hints(tmp_path / "2601.00001.pdf") == (
        IdentifierHint("arxiv", "2601.00001", "2601.00001", HintOrigin.filename),
    )


def test_encrypted_pdf_returns_filename_hint_only(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "2401.99999.pdf",
        metadata={"/doi": "10.1000/enc"},
        encrypted=True,
    )
    assert extract_identifier_hints(path) == (
        IdentifierHint("arxiv", "2401.99999", "2401.99999", HintOrigin.filename),
    )


def test_pypdf_logging_is_silenced_and_level_is_restored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("pypdf")
    original_level = logger.level
    logger.setLevel(logging.INFO)
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    try:
        with caplog.at_level(logging.DEBUG, logger="pypdf"):
            assert extract_identifier_hints(path) == ()
        assert caplog.records == []
        assert logger.level == logging.INFO
    finally:
        logger.setLevel(original_level)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.1234/abc.5678.", "10.1234/abc.5678"),
        ("10.1234/abc,", "10.1234/abc"),
        ("10.1234/abc;", "10.1234/abc"),
        ("10.1234/abc:", "10.1234/abc"),
        ("10.1234/abc]", "10.1234/abc"),
        ("10.1234/abc}", "10.1234/abc"),
        ("10.1234/abc)", "10.1234/abc"),
        ("10.1234/abc(5)", "10.1234/abc(5)"),
        ("10.1234/abc(5).", "10.1234/abc(5)"),
    ],
)
def test_strip_trailing_punctuation(value: str, expected: str) -> None:
    assert _strip_trailing_punctuation(value) == expected


def test_python_warnings_are_silenced() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with _quiet_pypdf():
            warnings.warn("noisy", UserWarning, stacklevel=2)
    assert caught == []
