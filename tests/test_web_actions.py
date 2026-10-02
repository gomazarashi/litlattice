import re
import uuid
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from litlattice.citations import list_all_citations
from litlattice.database import open_database
from litlattice.errors import ProviderError
from litlattice.installation import initialize
from litlattice.papers import create_paper, get_paper, list_papers
from litlattice.providers import ProviderWork
from litlattice.web import create_app

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "web_actions.db"
    initialize(path)
    return path


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
    def __init__(self, works=(), *, cites=None, results=None) -> None:
        self.works = list(works)
        self.results = list(works) if results is None else list(results)
        self.cites = dict(cites or {})
        self.lookup_calls: list[tuple[tuple[str, str], ...]] = []
        self.search_calls: list[tuple[str, int]] = []

    def lookup_work(self, identifiers):
        self.lookup_calls.append(tuple(identifiers))
        wanted = {(scheme, value.lower()) for scheme, value in identifiers}
        for candidate in self.works:
            keys = {(scheme, value.lower()) for scheme, value in candidate.identifiers}
            if keys & wanted:
                return candidate
        return None

    def search_works(self, query, *, limit):
        self.search_calls.append((query, limit))
        return self.results[:limit]

    @staticmethod
    def _key(item: ProviderWork) -> str:
        return item.identifiers[0][1]

    def fetch_references(self, item, *, limit):
        by_key = {self._key(work): work for work in self.works}
        keys = self.cites.get(self._key(item), [])
        return [by_key[key] for key in keys if key in by_key][:limit]

    def fetch_citations(self, item, *, limit):
        by_key = {self._key(work): work for work in self.works}
        citing = [key for key, refs in self.cites.items() if self._key(item) in refs]
        return [by_key[key] for key in citing if key in by_key][:limit]


class AlwaysPublished(FakeProvider):
    def lookup_work(self, identifiers):
        return self.works[0]


class FailingProvider(FakeProvider):
    def lookup_work(self, identifiers):
        raise ProviderError("OpenAlex request failed")


def _client(db_path: Path, provider) -> FlaskClient:
    app = create_app(
        db_path, secret_key="test-secret", provider_factory=lambda: provider
    )
    return app.test_client()


def _html(client: FlaskClient, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status
    return response.get_data(as_text=True)


def _csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match is not None
    return match.group(1)


def _seed(
    db_path: Path, *identifiers: tuple[str, str], title: str | None = None
) -> uuid.UUID:
    with open_database(db_path) as engine:
        return create_paper(engine, list(identifiers), title=title).id


def _papers(db_path: Path):
    with open_database(db_path) as engine:
        return list_papers(engine)


def _graph_provider() -> FakeProvider:
    return FakeProvider(
        [
            make_work("W1", "10.1/s", title="Seed", year=2020),
            make_work("W2", "10.1/r1", title="Ref one", year=2019),
            make_work("W3", "10.1/r2", title="Ref two", year=2018),
            make_work("W4", title="Citer one", year=2021),
            make_work("W5", "10.1/c2", title="Citer two", year=2022),
        ],
        cites={"W1": ["W2", "W3"], "W4": ["W1"], "W5": ["W1"]},
    )


def test_paper_add_page_shows_forms_and_nav(db_path: Path) -> None:
    client = _client(db_path, FakeProvider())

    html = _html(client, "/papers/new")

    assert "<h1>論文を追加</h1>" in html
    assert 'href="/papers/new" aria-current="page"' in html
    assert "識別子から取り込む" in html
    assert 'action="/papers/import"' in html
    assert "OpenAlex から取り込む" in html
    assert "タイトルで探す" in html
    assert 'action="/papers/new"' in html
    assert "OpenAlex で検索" in html
    assert "何も保存しません" in html
    assert 'name="csrf_token" value="' in html


def test_papers_page_links_to_paper_add(db_path: Path) -> None:
    client = _client(db_path, FakeProvider())

    html = _html(client, "/papers")

    assert '<a href="/papers/new">論文を追加</a>' in html


def test_search_shows_candidates_and_stores_nothing(db_path: Path) -> None:
    existing = _seed(db_path, ("doi", "10.1/known"), title="Known")
    ambiguous_one = _seed(db_path, ("doi", "10.1/amb1"), title="Amb one")
    ambiguous_two = _seed(db_path, ("doi", "10.2/amb2"), title="Amb two")
    twin = ProviderWork(
        (("openalex", "W4"), ("doi", "10.1/amb1"), ("doi", "10.2/amb2")), "Twin", 2020
    )
    provider = FakeProvider(
        results=[
            make_work("W1", "10.1/known", title="Known work", year=2019),
            make_work("W2", "10.1/fresh", title="Fresh work", year=2021),
            ProviderWork((("doi", "10.1/onlydoi"),), "DOI only", 2018),
            twin,
        ]
    )
    client = _client(db_path, provider)

    html = _html(client, "/papers/new?q=graph")

    assert "Known work" in html
    assert "Fresh work" in html
    assert "登録済み" in html
    assert "新規" in html
    assert "複数に一致" in html
    assert f'href="/papers/{existing}"' in html
    assert 'name="scheme" value="openalex"' in html
    assert 'name="value" value="W2"' in html
    assert 'name="scheme" value="doi"' in html
    assert 'name="value" value="10.1/onlydoi"' in html
    assert f'href="/papers/{ambiguous_one}"' in html
    assert f'href="/papers/{ambiguous_two}"' in html
    assert html.count(">取り込む<") == 2
    assert provider.search_calls == [("graph", 10)]
    assert len(_papers(db_path)) == 3


def test_search_without_candidates(db_path: Path) -> None:
    provider = FakeProvider(results=[])
    client = _client(db_path, provider)

    html = _html(client, "/papers/new?q=nothing")

    assert "見つかりませんでした。" in html
    assert provider.search_calls == [("nothing", 10)]


def test_search_blank_query_does_not_call_provider(db_path: Path) -> None:
    provider = FakeProvider(results=[make_work("W1", "10.1/a", title="Alpha")])
    client = _client(db_path, provider)

    for url in ("/papers/new", "/papers/new?q=", "/papers/new?q=%20%20"):
        html = _html(client, url)
        assert "見つかりませんでした。" not in html

    assert provider.search_calls == []


def test_import_created_then_matched(db_path: Path) -> None:
    provider = FakeProvider([make_work("W1", "10.1/s", title="Seed", year=2020)])
    client = _client(db_path, provider)
    token = _csrf(_html(client, "/papers/new"))

    created = client.post(
        "/papers/import",
        data={"scheme": "doi", "value": "10.1/s", "csrf_token": token},
    )

    assert created.status_code == 303
    location = created.headers["Location"]
    assert location.startswith("/papers/")
    html = _html(client, location)
    assert "論文を取り込みました。ライブラリには追加していません。" in html
    assert "完了:" in html
    papers = _papers(db_path)
    assert len(papers) == 1
    assert papers[0].title == "Seed"
    assert papers[0].in_library is False

    token = _csrf(_html(client, "/papers/new"))
    matched = client.post(
        "/papers/import",
        data={"scheme": "doi", "value": "10.1/s", "csrf_token": token},
    )

    assert matched.status_code == 303
    assert matched.headers["Location"] == location
    html = _html(client, matched.headers["Location"])
    assert "既存の論文に一致しました。" in html
    assert len(_papers(db_path)) == 1


def test_import_ambiguous_redirects_to_paper_add(db_path: Path) -> None:
    _seed(db_path, ("doi", "10.1/a"), title="A")
    _seed(db_path, ("doi", "10.2/b"), title="B")
    twin = ProviderWork(
        (("openalex", "W9"), ("doi", "10.1/a"), ("doi", "10.2/b")), "Twin", 2020
    )
    client = _client(db_path, FakeProvider([twin]))
    token = _csrf(_html(client, "/papers/new"))

    response = client.post(
        "/papers/import",
        data={"scheme": "doi", "value": "10.1/a", "csrf_token": token},
    )

    assert response.status_code == 303
    assert response.headers["Location"].endswith("/papers/new")
    html = _html(client, "/papers/new")
    assert "複数の既存論文" in html
    assert "注意:" in html
    assert len(_papers(db_path)) == 2


def test_import_unconfirmed_redirects_to_paper_add(db_path: Path) -> None:
    published = ProviderWork(
        (("openalex", "W1"), ("doi", "10.1/published")), "Published", 2021
    )
    client = _client(db_path, AlwaysPublished([published]))
    token = _csrf(_html(client, "/papers/new"))

    response = client.post(
        "/papers/import",
        data={"scheme": "arxiv", "value": "2401.12345", "csrf_token": token},
    )

    assert response.status_code == 303
    assert response.headers["Location"].endswith("/papers/new")
    html = _html(client, "/papers/new")
    assert "同じ論文と確認できませんでした" in html
    assert "何も保存していません" in html
    assert _papers(db_path) == []


def test_import_unknown_work_redirects_to_paper_add(db_path: Path) -> None:
    client = _client(db_path, FakeProvider())
    token = _csrf(_html(client, "/papers/new"))

    response = client.post(
        "/papers/import",
        data={"scheme": "doi", "value": "10.1/missing", "csrf_token": token},
    )

    assert response.status_code == 303
    assert response.headers["Location"].endswith("/papers/new")
    html = _html(client, "/papers/new")
    assert "OpenAlex に該当する論文がありませんでした" in html
    assert "エラー:" in html


def test_import_invalid_doi_reports_error_without_provider_call(
    db_path: Path,
) -> None:
    provider = FakeProvider()
    client = _client(db_path, provider)
    token = _csrf(_html(client, "/papers/new"))

    response = client.post(
        "/papers/import",
        data={"scheme": "doi", "value": "not-a-doi", "csrf_token": token},
    )

    assert response.status_code == 303
    html = _html(client, "/papers/new")
    assert "Invalid doi identifier" in html
    assert provider.lookup_calls == []


def test_import_invalid_scheme_reports_error_without_provider_call(
    db_path: Path,
) -> None:
    provider = FakeProvider()
    client = _client(db_path, provider)
    token = _csrf(_html(client, "/papers/new"))

    response = client.post(
        "/papers/import",
        data={"scheme": "pmid", "value": "12345", "csrf_token": token},
    )

    assert response.status_code == 303
    assert response.headers["Location"].endswith("/papers/new")
    html = _html(client, "/papers/new")
    assert "識別子の種類と値を入力してください" in html
    assert provider.lookup_calls == []


def test_posts_without_csrf_token_are_rejected(db_path: Path) -> None:
    paper_id = _seed(db_path, ("doi", "10.1/s"))
    client = _client(db_path, FakeProvider([make_work("W1", "10.1/s")]))

    import_response = client.post(
        "/papers/import", data={"scheme": "doi", "value": "10.1/s"}
    )
    fetch_response = client.post(f"/papers/{paper_id}/fetch")
    expand_response = client.post(
        f"/papers/{paper_id}/expand",
        data={"depth": "1", "direction": "both", "max_nodes": "50"},
    )

    for response in (import_response, fetch_response, expand_response):
        assert response.status_code == 400
        assert "フォームを送信できませんでした" in response.get_data(as_text=True)


def test_fetch_updates_metadata(db_path: Path) -> None:
    paper_id = _seed(db_path, ("doi", "10.1/s"))
    client = _client(
        db_path, FakeProvider([make_work("W1", "10.1/s", title="Seed", year=2020)])
    )
    token = _csrf(_html(client, f"/papers/{paper_id}"))

    response = client.post(f"/papers/{paper_id}/fetch", data={"csrf_token": token})

    assert response.status_code == 303
    assert response.headers["Location"].endswith(f"/papers/{paper_id}")
    html = _html(client, f"/papers/{paper_id}")
    assert "OpenAlex から metadata を取得しました" in html
    assert "タイトル" in html
    assert "出版年" in html
    assert "追加した識別子: openalex:W1" in html
    with open_database(db_path) as engine:
        paper = get_paper(engine, paper_id)
    assert paper.title == "Seed"
    assert paper.publication_year == 2020
    assert paper.in_library is False


def test_fetch_unconfirmed_warns_and_keeps_metadata(db_path: Path) -> None:
    published = ProviderWork(
        (("openalex", "W1"), ("doi", "10.1/published")), "Published", 2021
    )
    paper_id = _seed(db_path, ("arxiv", "2401.12345"))
    client = _client(db_path, AlwaysPublished([published]))
    token = _csrf(_html(client, f"/papers/{paper_id}"))

    response = client.post(f"/papers/{paper_id}/fetch", data={"csrf_token": token})

    assert response.status_code == 303
    html = _html(client, f"/papers/{paper_id}")
    assert "同じ論文と確認できませんでした" in html
    assert "metadata は適用していません" in html
    with open_database(db_path) as engine:
        assert get_paper(engine, paper_id).title is None


def test_fetch_without_identifiers_reports_error(db_path: Path) -> None:
    paper_id = _seed(db_path)
    client = _client(db_path, FakeProvider())
    token = _csrf(_html(client, f"/papers/{paper_id}"))

    response = client.post(f"/papers/{paper_id}/fetch", data={"csrf_token": token})

    assert response.status_code == 303
    html = _html(client, f"/papers/{paper_id}")
    assert "外部識別子がないため OpenAlex で探せません。" in html
    assert "エラー:" in html


def test_expand_saves_citations_and_redirects_to_graph(db_path: Path) -> None:
    paper_id = _seed(db_path, ("doi", "10.1/s"))
    provider = _graph_provider()
    client = _client(db_path, provider)
    token = _csrf(_html(client, f"/papers/{paper_id}"))

    response = client.post(
        f"/papers/{paper_id}/expand",
        data={
            "depth": "1",
            "direction": "references",
            "max_nodes": "50",
            "csrf_token": token,
        },
    )

    assert response.status_code == 303
    location = response.headers["Location"]
    assert location.startswith(f"/papers/{paper_id}/graph")
    assert "depth=1" in location
    assert "direction=references" in location

    html = _html(client, location)
    assert (
        "論文 2 件を新たに保存し、引用 2 件を追加しました（既存 0 件・確認 0 件）。"
        "ライブラリには追加していません。" in html
    )
    with open_database(db_path) as engine:
        citations = list_all_citations(engine)
        papers = list_papers(engine)
    assert len(citations) == 2
    assert len(papers) == 3
    assert all(not paper.in_library for paper in papers)


def test_expand_rejects_invalid_options(db_path: Path) -> None:
    paper_id = _seed(db_path, ("doi", "10.1/s"))
    provider = _graph_provider()
    client = _client(db_path, provider)
    token = _csrf(_html(client, f"/papers/{paper_id}"))

    invalid = [
        {"depth": "3", "direction": "both", "max_nodes": "50"},
        {"depth": "1", "direction": "both", "max_nodes": "500"},
        {"depth": "1", "direction": "sideways", "max_nodes": "50"},
    ]
    for options in invalid:
        response = client.post(
            f"/papers/{paper_id}/expand", data={**options, "csrf_token": token}
        )
        assert response.status_code == 400
        html = response.get_data(as_text=True)
        assert "リクエストが正しくありません" in html
        assert "上限は 20 / 50 / 100" in html

    assert provider.lookup_calls == []


def test_expand_reports_truncation(db_path: Path) -> None:
    references = [
        make_work(f"W{10 + index}", f"10.9/r{index}", title=f"Ref {index}")
        for index in range(10)
    ]
    provider = FakeProvider(
        [make_work("W1", "10.1/s", title="Seed")] + references,
        cites={"W1": [reference.identifiers[0][1] for reference in references]},
    )
    paper_id = _seed(db_path, ("doi", "10.1/s"))
    client = _client(db_path, provider)
    token = _csrf(_html(client, f"/papers/{paper_id}"))

    response = client.post(
        f"/papers/{paper_id}/expand",
        data={
            "depth": "1",
            "direction": "both",
            "max_nodes": "20",
            "csrf_token": token,
        },
    )

    assert response.status_code == 303
    html = _html(client, response.headers["Location"])
    assert "上限（20 件）で探索を打ち切りました。" in html
    assert "注意:" in html


def test_provider_error_on_import(db_path: Path) -> None:
    client = _client(db_path, FailingProvider())
    token = _csrf(_html(client, "/papers/new"))

    response = client.post(
        "/papers/import",
        data={"scheme": "doi", "value": "10.1/s", "csrf_token": token},
    )

    assert response.status_code == 303
    assert response.headers["Location"].endswith("/papers/new")
    html = _html(client, "/papers/new")
    assert "OpenAlex と通信できませんでした: OpenAlex request failed" in html
    assert "エラー:" in html


def test_provider_error_on_fetch_and_expand(db_path: Path) -> None:
    paper_id = _seed(db_path, ("doi", "10.1/s"))
    client = _client(db_path, FailingProvider())

    for endpoint in ("fetch", "expand"):
        token = _csrf(_html(client, f"/papers/{paper_id}"))
        data: dict[str, str] = {"csrf_token": token}
        if endpoint == "expand":
            data.update({"depth": "1", "direction": "both", "max_nodes": "50"})
        response = client.post(f"/papers/{paper_id}/{endpoint}", data=data)

        assert response.status_code == 303
        assert response.headers["Location"].endswith(f"/papers/{paper_id}")
        html = _html(client, f"/papers/{paper_id}")
        assert "OpenAlex と通信できませんでした: OpenAlex request failed" in html
