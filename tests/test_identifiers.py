import pytest

from litlattice.identifiers import normalize_identifier


@pytest.mark.parametrize(
    "value",
    [
        "10.1000/ABC",
        "doi:10.1000/ABC",
        "DOI:10.1000/abc",
        "https://doi.org/10.1000/ABC",
        "http://doi.org/10.1000/ABC",
        "https://dx.doi.org/10.1000/ABC",
        "  10.1000/abc  ",
    ],
)
def test_doi_forms_normalize_to_same_value(value: str) -> None:
    assert normalize_identifier("doi", value) == ("doi", "10.1000/abc")


def test_doi_non_ascii_is_left_untouched() -> None:
    assert normalize_identifier("doi", "10.1000/ÄBC") == ("doi", "10.1000/Äbc")


@pytest.mark.parametrize("value", ["", "abc", "https://example.org/10.1000/x"])
def test_invalid_doi_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_identifier("doi", value)


@pytest.mark.parametrize(
    "value",
    [
        "arXiv:2401.12345",
        "2401.12345",
        "https://arxiv.org/abs/2401.12345",
        "https://arxiv.org/abs/2401.12345v3",
        "2401.12345v1",
        "2401.12345v2",
    ],
)
def test_arxiv_forms_normalize_to_base_id(value: str) -> None:
    assert normalize_identifier("arxiv", value) == ("arxiv", "2401.12345")


def test_old_style_arxiv_id() -> None:
    assert normalize_identifier("arXiv", "arXiv:hep-th/9901001v2") == (
        "arxiv",
        "hep-th/9901001",
    )


@pytest.mark.parametrize("value", ["", "2401.123", "not-an-id"])
def test_invalid_arxiv_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_identifier("arxiv", value)


def test_unknown_scheme_only_strips_whitespace() -> None:
    assert normalize_identifier(" PubMed ", "  PMC123abc  ") == (
        "pubmed",
        "PMC123abc",
    )


@pytest.mark.parametrize(
    "value",
    [
        "W2741809807",
        "w2741809807",
        "openalex:W2741809807",
        "https://openalex.org/W2741809807",
        "https://api.openalex.org/works/W2741809807",
    ],
)
def test_openalex_forms_normalize_to_work_id(value: str) -> None:
    assert normalize_identifier("OpenAlex", value) == ("openalex", "W2741809807")


@pytest.mark.parametrize("value", ["", "A123", "W12x", "https://openalex.org/A1"])
def test_invalid_openalex_id_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_identifier("openalex", value)


@pytest.mark.parametrize("scheme", ["", "  ", "has space", "-lead"])
def test_invalid_scheme_is_rejected(scheme: str) -> None:
    with pytest.raises(ValueError):
        normalize_identifier(scheme, "x")
