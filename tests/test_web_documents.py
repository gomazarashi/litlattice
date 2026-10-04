import re
import uuid
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from pdf_factory import write_pdf

from litlattice.database import open_database
from litlattice.documents import list_documents
from litlattice.errors import ProviderError
from litlattice.installation import initialize
from litlattice.papers import create_paper, list_papers
from litlattice.providers import ProviderWork
from litlattice.scan import scan_sources
from litlattice.sources import add_source
from litlattice.web import create_app

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "web_documents.db"
    initialize(path)
    return path


@pytest.fixture
def pdf_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "pdfs"
    directory.mkdir()
    return directory


def make_work(
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
    def __init__(self, works=()) -> None:
        self.works = list(works)
        self.lookup_calls: list[tuple[tuple[str, str], ...]] = []

    def lookup_work(self, identifiers):
        self.lookup_calls.append(tuple(identifiers))
        wanted = {(scheme, value.lower()) for scheme, value in identifiers}
        for candidate in self.works:
            keys = {(scheme, value.lower()) for scheme, value in candidate.identifiers}
            if keys & wanted:
                return candidate
        return None


class AlwaysPublished(FakeProvider):
    def lookup_work(self, identifiers):
        return self.works[0]


class FailingProvider(FakeProvider):
    def lookup_work(self, identifiers):
        raise ProviderError("OpenAlex request failed")


def _client(db_path: Path, provider: FakeProvider | None = None) -> FlaskClient:
    fake = provider if provider is not None else FakeProvider()
    app = create_app(db_path, secret_key="test-secret", provider_factory=lambda: fake)
    return app.test_client()


def _html(client: FlaskClient, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status
    return response.get_data(as_text=True)


def _csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match is not None
    return match.group(1)


def _scan(db_path: Path, directory: Path) -> None:
    with open_database(db_path) as engine:
        add_source(engine, directory)
        scan_sources(engine)


def _documents(db_path: Path, **kwargs):
    with open_database(db_path) as engine:
        return list_documents(engine, **kwargs)


def _papers(db_path: Path):
    with open_database(db_path) as engine:
        return list_papers(engine)


def _library_count(db_path: Path) -> int:
    return sum(paper.in_library for paper in _papers(db_path))


def test_documents_list_filters_unlinked(db_path: Path, pdf_dir: Path) -> None:
    write_pdf(pdf_dir / "2401.12345.pdf")
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    client = _client(db_path)

    html = _html(client, "/documents")

    assert '<a href="/documents" aria-current="page"' in html
    assert str(pdf_dir / "2401.12345.pdf") in html
    assert str(pdf_dir / "unknown.pdf") in html
    assert "arxiv:2401.12345" in html
    assert "未関連付け" in html

    unlinked = _html(client, "/documents?unlinked=1")

    assert str(pdf_dir / "unknown.pdf") in unlinked
    assert str(pdf_dir / "2401.12345.pdf") not in unlinked
    assert "未関連付けのPDFはありません" not in unlinked


def test_documents_list_empty_states(db_path: Path) -> None:
    client = _client(db_path)

    assert "PDFが登録されていません" in _html(client, "/documents")
    assert "未関連付けのPDFはありません" in _html(client, "/documents?unlinked=1")


def test_document_detail_shows_hints_assessment_and_resolution(
    db_path: Path, pdf_dir: Path
) -> None:
    write_pdf(pdf_dir / "2401.12345.pdf", metadata={"/doi": "10.5555/one"})
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    client = _client(db_path)

    html = _html(client, f"/documents/{document.id}")

    assert '<a href="/documents" aria-current="page"' in html
    assert str(pdf_dir / "2401.12345.pdf") in html
    assert "ファイルを利用できます" in html
    assert "filename" in html
    assert "arxiv:2401.12345" in html
    assert "pdf_metadata" in html
    assert "doi:10.5555/one" in html
    assert "強い" in html
    assert "ambiguous" in html
    assert "候補が複数あるため、自動では関連付けません" in html
    assert html.count(f'action="/documents/{document.id}/import"') == 3
    assert 'name="scheme" value="arxiv"' in html
    assert 'name="value" value="2401.12345"' in html
    assert 'name="scheme" value="doi"' in html
    assert 'name="value" value="10.5555/one"' in html
    assert 'href="/papers/new"' in html
    assert 'name="paper_id"' in html
    assert _papers(db_path) == []
    assert _documents(db_path)[0].paper_id is None


def test_document_detail_offers_candidate_papers(db_path: Path, pdf_dir: Path) -> None:
    with open_database(db_path) as engine:
        preprint = create_paper(engine, [("arxiv", "2401.12345")])
        published = create_paper(engine, [("doi", "10.5555/one")])
    write_pdf(pdf_dir / "2401.12345.pdf", metadata={"/doi": "10.5555/one"})
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    client = _client(db_path)

    html = _html(client, f"/documents/{document.id}")

    assert "候補の論文" in html
    assert f'href="/papers/{preprint.id}"' in html
    assert f'href="/papers/{published.id}"' in html
    assert f'value="{preprint.id}"' in html
    assert f'value="{published.id}"' in html
    assert html.count("この論文に関連付ける") == 2
    assert _documents(db_path)[0].paper_id is None


def test_link_is_idempotent_and_unlink_keeps_records(
    db_path: Path, pdf_dir: Path
) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    with open_database(db_path) as engine:
        paper = create_paper(engine, [], title="Chosen by hand")
    client = _client(db_path)

    token = _csrf(_html(client, f"/documents/{document.id}"))
    first = client.post(
        f"/documents/{document.id}/link",
        data={"paper_id": str(paper.id), "csrf_token": token},
    )

    assert first.status_code == 303
    assert first.headers["Location"].endswith(f"/documents/{document.id}")
    detail = _html(client, f"/documents/{document.id}")
    assert "PDF を論文に関連付けました。ライブラリには追加していません。" in detail
    assert "Chosen by hand" in detail
    assert "関連付けを解除" in detail
    assert _documents(db_path)[0].paper_id == paper.id
    assert _papers(db_path)[0].in_library is False
    assert _library_count(db_path) == 0
    assert (pdf_dir / "unknown.pdf").exists()

    token = _csrf(detail)
    again = client.post(
        f"/documents/{document.id}/link",
        data={"paper_id": str(paper.id), "csrf_token": token},
    )

    assert again.status_code == 303
    again_html = _html(client, f"/documents/{document.id}")
    assert "既にその論文に関連付けられています" in again_html
    assert _documents(db_path)[0].paper_id == paper.id

    token = _csrf(again_html)
    unlinked = client.post(
        f"/documents/{document.id}/unlink",
        data={"paper_id": str(paper.id), "csrf_token": token},
    )

    assert unlinked.status_code == 303
    unlinked_html = _html(client, f"/documents/{document.id}")
    assert "論文との関連付けを解除しました。PDF と論文は残っています。" in unlinked_html
    assert _documents(db_path)[0].paper_id is None
    assert _papers(db_path)[0].id == paper.id
    assert (pdf_dir / "unknown.pdf").exists()

    token = _csrf(unlinked_html)
    repeat = client.post(
        f"/documents/{document.id}/unlink",
        data={"paper_id": str(paper.id), "csrf_token": token},
    )

    assert repeat.status_code == 303
    assert "関連付けられていません" in _html(client, f"/documents/{document.id}")


def test_link_validates_paper_id_and_reports_missing_paper(
    db_path: Path, pdf_dir: Path
) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    client = _client(db_path)
    token = _csrf(_html(client, f"/documents/{document.id}"))

    invalid = client.post(
        f"/documents/{document.id}/link",
        data={"paper_id": "not-a-uuid", "csrf_token": token},
    )

    assert invalid.status_code == 400
    assert "リクエストが正しくありません" in invalid.get_data(as_text=True)

    missing = client.post(
        f"/documents/{document.id}/link",
        data={"paper_id": str(uuid.uuid4()), "csrf_token": token},
    )

    assert missing.status_code == 404
    assert "論文が見つかりません" in missing.get_data(as_text=True)

    invalid_unlink = client.post(
        f"/documents/{document.id}/unlink",
        data={"paper_id": "not-a-uuid", "csrf_token": token},
    )

    assert invalid_unlink.status_code == 400
    assert _documents(db_path)[0].paper_id is None


def test_missing_document_is_404_for_every_route(db_path: Path) -> None:
    client = _client(db_path)
    unknown = uuid.uuid4()
    with open_database(db_path) as engine:
        paper = create_paper(engine, [], title="Any")
    token = _csrf(_html(client, f"/papers/{paper.id}"))

    detail = client.get(f"/documents/{unknown}")

    assert detail.status_code == 404
    assert "PDF が見つかりません" in detail.get_data(as_text=True)

    link = client.post(
        f"/documents/{unknown}/link",
        data={"paper_id": str(uuid.uuid4()), "csrf_token": token},
    )
    unlink = client.post(
        f"/documents/{unknown}/unlink",
        data={"paper_id": str(uuid.uuid4()), "csrf_token": token},
    )
    imported = client.post(
        f"/documents/{unknown}/import",
        data={"scheme": "bogus!", "value": "x", "csrf_token": token},
    )

    assert link.status_code == 404
    assert unlink.status_code == 404
    assert imported.status_code == 404
    assert "PDF が見つかりません" in imported.get_data(as_text=True)


def test_import_creates_paper_links_and_never_adds_to_library(
    db_path: Path, pdf_dir: Path
) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    provider = FakeProvider([make_work("W1", "10.1/s", title="Seed", year=2020)])
    client = _client(db_path, provider)
    token = _csrf(_html(client, f"/documents/{document.id}"))

    response = client.post(
        f"/documents/{document.id}/import",
        data={"scheme": "doi", "value": "10.1/s", "csrf_token": token},
    )

    assert response.status_code == 303
    assert response.headers["Location"].endswith(f"/documents/{document.id}")
    html = _html(client, response.headers["Location"])
    assert "論文を取り込みました。ライブラリには追加していません。" in html
    assert "PDF を論文に関連付けました。ライブラリには追加していません。" in html
    assert "Seed" in html
    assert provider.lookup_calls == [(("doi", "10.1/s"),)]
    (paper,) = _papers(db_path)
    assert paper.title == "Seed"
    assert paper.in_library is False
    assert _library_count(db_path) == 0
    assert _documents(db_path)[0].paper_id == paper.id


def test_import_matches_existing_paper_and_links(db_path: Path, pdf_dir: Path) -> None:
    with open_database(db_path) as engine:
        paper = create_paper(engine, [("doi", "10.1/s")], title="Known")
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    provider = FakeProvider([make_work("W1", "10.1/s", title="Seed", year=2020)])
    client = _client(db_path, provider)
    token = _csrf(_html(client, f"/documents/{document.id}"))

    response = client.post(
        f"/documents/{document.id}/import",
        data={"scheme": "doi", "value": "10.1/s", "csrf_token": token},
    )

    assert response.status_code == 303
    html = _html(client, f"/documents/{document.id}")
    assert "既存の論文に一致しました。" in html
    assert "追加した識別子: openalex:W1" in html
    assert "PDF を論文に関連付けました" in html
    assert [p.id for p in _papers(db_path)] == [paper.id]
    assert _documents(db_path)[0].paper_id == paper.id
    assert _library_count(db_path) == 0


def test_import_unconfirmed_warns_and_leaves_unlinked(
    db_path: Path, pdf_dir: Path
) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    published = ProviderWork(
        (("openalex", "W1"), ("doi", "10.1/published")), "Published", 2021
    )
    client = _client(db_path, AlwaysPublished([published]))
    token = _csrf(_html(client, f"/documents/{document.id}"))

    response = client.post(
        f"/documents/{document.id}/import",
        data={"scheme": "arxiv", "value": "2401.12345", "csrf_token": token},
    )

    assert response.status_code == 303
    html = _html(client, f"/documents/{document.id}")
    assert "同じ論文と確認できませんでした" in html
    assert "何も保存していません" in html
    assert _papers(db_path) == []
    assert _documents(db_path)[0].paper_id is None


def test_import_ambiguous_warns_and_leaves_unlinked(
    db_path: Path, pdf_dir: Path
) -> None:
    with open_database(db_path) as engine:
        create_paper(engine, [("doi", "10.1/a")], title="A")
        create_paper(engine, [("doi", "10.2/b")], title="B")
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    twin = ProviderWork(
        (("openalex", "W9"), ("doi", "10.1/a"), ("doi", "10.2/b")), "Twin", 2020
    )
    client = _client(db_path, FakeProvider([twin]))
    token = _csrf(_html(client, f"/documents/{document.id}"))

    response = client.post(
        f"/documents/{document.id}/import",
        data={"scheme": "doi", "value": "10.1/a", "csrf_token": token},
    )

    assert response.status_code == 303
    html = _html(client, f"/documents/{document.id}")
    assert "複数の既存論文" in html
    assert len(_papers(db_path)) == 2
    assert _documents(db_path)[0].paper_id is None


def test_import_provider_failure_flashes_error(db_path: Path, pdf_dir: Path) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    client = _client(db_path, FailingProvider())
    token = _csrf(_html(client, f"/documents/{document.id}"))

    response = client.post(
        f"/documents/{document.id}/import",
        data={"scheme": "doi", "value": "10.1/s", "csrf_token": token},
    )

    assert response.status_code == 303
    html = _html(client, f"/documents/{document.id}")
    assert "OpenAlex と通信できませんでした: OpenAlex request failed" in html
    assert _papers(db_path) == []
    assert _documents(db_path)[0].paper_id is None


def test_import_unknown_work_flashes_error(db_path: Path, pdf_dir: Path) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    provider = FakeProvider()
    client = _client(db_path, provider)
    token = _csrf(_html(client, f"/documents/{document.id}"))

    response = client.post(
        f"/documents/{document.id}/import",
        data={"scheme": "doi", "value": "10.1/missing", "csrf_token": token},
    )

    assert response.status_code == 303
    html = _html(client, f"/documents/{document.id}")
    assert "OpenAlex に該当する論文がありませんでした" in html
    assert provider.lookup_calls == [(("doi", "10.1/missing"),)]
    assert _papers(db_path) == []


def test_import_invalid_identifier_reports_error_without_provider_call(
    db_path: Path, pdf_dir: Path
) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    provider = FakeProvider()
    client = _client(db_path, provider)
    token = _csrf(_html(client, f"/documents/{document.id}"))

    response = client.post(
        f"/documents/{document.id}/import",
        data={"scheme": "doi", "value": "not-a-doi", "csrf_token": token},
    )

    assert response.status_code == 303
    html = _html(client, f"/documents/{document.id}")
    assert "Invalid doi identifier" in html
    assert provider.lookup_calls == []
    assert _papers(db_path) == []
    assert _documents(db_path)[0].paper_id is None


def test_document_posts_require_csrf_token(db_path: Path, pdf_dir: Path) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    with open_database(db_path) as engine:
        paper = create_paper(engine, [], title="Chosen")
    client = _client(db_path)

    for url, data in (
        (f"/documents/{document.id}/link", {"paper_id": str(paper.id)}),
        (f"/documents/{document.id}/unlink", {"paper_id": str(paper.id)}),
        (f"/documents/{document.id}/import", {"scheme": "doi", "value": "10.1/s"}),
    ):
        response = client.post(url, data=data)
        assert response.status_code == 400
        assert "フォームを送信できませんでした" in response.get_data(as_text=True)

    assert _documents(db_path)[0].paper_id is None
    assert _library_count(db_path) == 0


def test_document_detail_reports_unavailable_files(
    db_path: Path, pdf_dir: Path
) -> None:
    pdf = write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    pdf.unlink()
    client = _client(db_path)

    html = _html(client, f"/documents/{document.id}")

    assert "ファイルが見つかりません" in html
    assert "未関連付け" in html


def test_paper_detail_links_documents_to_details(db_path: Path, pdf_dir: Path) -> None:
    write_pdf(pdf_dir / "2401.12345.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    paper_id = document.paper_id
    assert paper_id is not None
    client = _client(db_path)

    html = _html(client, f"/papers/{paper_id}")

    assert f'href="/documents/{document.id}"' in html


def test_document_views_escape_titles_and_paths(db_path: Path, pdf_dir: Path) -> None:
    write_pdf(pdf_dir / "<img src=x onerror=alert(1)>.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    with open_database(db_path) as engine:
        paper = create_paper(engine, [], title='<script>alert("x")</script>')
    client = _client(db_path)
    token = _csrf(_html(client, f"/documents/{document.id}"))
    client.post(
        f"/documents/{document.id}/link",
        data={"paper_id": str(paper.id), "csrf_token": token},
    )

    listing = _html(client, "/documents")
    detail = _html(client, f"/documents/{document.id}")

    for page in (listing, detail):
        assert "<img src=x" not in page
        assert "<script>alert" not in page
        assert "&lt;img src=x onerror=alert(1)&gt;.pdf" in page
        assert "&lt;script&gt;alert" in page


@pytest.mark.parametrize("scheme,value", [("pmid", "12345"), ("doi", ""), ("", "x")])
def test_document_import_rejects_unsupported_or_empty_input(
    db_path: Path, pdf_dir: Path, scheme: str, value: str
) -> None:
    write_pdf(pdf_dir / "unknown.pdf")
    _scan(db_path, pdf_dir)
    (document,) = _documents(db_path)
    provider = FakeProvider()
    client = _client(db_path, provider)
    token = _csrf(_html(client, f"/documents/{document.id}"))
    response = client.post(
        f"/documents/{document.id}/import",
        data={"scheme": scheme, "value": value, "csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "識別子の種類と値を入力してください。" in response.get_data(as_text=True)
    assert provider.lookup_calls == []
    assert _papers(db_path) == []
    assert _documents(db_path)[0].paper_id is None
