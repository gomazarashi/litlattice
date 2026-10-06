import re
import uuid
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from litlattice.citations import add_citation
from litlattice.database import open_database
from litlattice.installation import initialize
from litlattice.papers import add_to_library, create_paper, get_paper
from litlattice.web import create_app

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "web.db"
    initialize(path)
    return path


@pytest.fixture
def papers(db_path: Path) -> dict[str, uuid.UUID]:
    with open_database(db_path) as engine:
        a = create_paper(engine, [("doi", "10.5555/a")], title="Alpha Paper").id
        b = create_paper(engine, [("arxiv", "2401.00002")], title="Beta Paper").id
        c = create_paper(engine).id
        add_to_library(engine, a)
        add_citation(engine, a, b)
        add_citation(engine, b, c)
    return {"a": a, "b": b, "c": c}


@pytest.fixture
def client(db_path: Path) -> FlaskClient:
    return create_app(db_path, secret_key="test-secret").test_client()


def _html(client: FlaskClient, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status
    return response.get_data(as_text=True)


def _csrf_token(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match is not None
    return match.group(1)


def test_dashboard_shows_counts(client: FlaskClient, papers) -> None:
    html = _html(client, "/")

    assert '<html lang="ja">' in html
    assert "登録論文数" in html
    assert '<dd><a href="/papers">3</a></dd>' in html
    assert "<dd>1</dd>" in html  # in library
    assert "<dd>2</dd>" in html  # citations
    assert '<dd><a href="/graph">2</a></dd>' not in html


def test_papers_lists_all_papers_with_library_state(
    client: FlaskClient, papers
) -> None:
    html = _html(client, "/papers")

    assert "Alpha Paper" in html
    assert "Beta Paper" in html
    assert "（無題）" in html
    assert "doi:10.5555/a" in html
    assert html.count("ライブラリ登録済み") == 1
    assert html.count("ライブラリ未登録") == 2


def test_paper_detail_shows_references_and_cited_by(
    client: FlaskClient, papers
) -> None:
    html = _html(client, f"/papers/{papers['b']}")

    assert "Beta Paper" in html
    assert str(papers["b"]) in html
    assert "arxiv:2401.00002" in html
    references, cited_by = html.split('id="cited-by"')
    assert f"/papers/{papers['c']}" in references.split('id="refs"')[1]
    assert f"/papers/{papers['a']}" in cited_by
    assert "ライブラリに追加" in html


def test_graph_page_shows_library_graph(client: FlaskClient, papers) -> None:
    html = _html(client, "/graph")

    assert "引用グラフ" in html
    assert "Alpha Paper" in html
    assert "Beta Paper" not in html
    assert "<svg" not in html
    assert "vendor/cytoscape/cytoscape.min.js" in html
    assert "graph.js" in html
    assert "https://" not in html
    assert "//cdn" not in html
    assert 'data-api-url="/api/graph/library"' in html


def test_graph_page_with_empty_library(client: FlaskClient, db_path: Path) -> None:
    html = _html(client, "/graph")

    assert "ライブラリに論文がありません" in html
    assert 'id="graph-canvas"' not in html


def test_paper_graph_page(client: FlaskClient, papers) -> None:
    html = _html(client, f"/papers/{papers['b']}/graph")

    assert "周辺グラフ: Beta Paper" in html
    breadcrumb = html.split('class="breadcrumb"')[1].split("</p>")[0]
    assert f'href="/papers/{papers["b"]}"' in breadcrumb
    assert 'id="depth"' in html
    assert 'id="direction"' in html
    assert (
        f'data-api-url="/api/graph/neighborhood/{papers["b"]}?depth=1&amp;direction=both"'
        in html
    )
    assert f'data-seed-id="{papers["b"]}"' in html


def test_paper_graph_page_selected_options(client: FlaskClient, papers) -> None:
    html = _html(client, f"/papers/{papers['b']}/graph?depth=2&direction=references")

    assert (
        f'data-api-url="/api/graph/neighborhood/{papers["b"]}'
        '?depth=2&amp;direction=references"'
    ) in html
    assert re.search(r'<option value="2"[^>]* selected', html)
    assert re.search(r'<option value="references"[^>]* selected', html)


@pytest.mark.parametrize("query", ["depth=3", "depth=abc", "direction=sideways"])
def test_paper_graph_rejects_invalid_options(
    client: FlaskClient, papers, query: str
) -> None:
    html = _html(client, f"/papers/{papers['b']}/graph?{query}", status=400)

    assert "リクエストが正しくありません" in html
    assert "深さは 1 または 2" in html


def test_paper_graph_unknown_paper_is_404(client: FlaskClient, papers) -> None:
    html = _html(client, f"/papers/{uuid.uuid4()}/graph", status=404)

    assert "論文が見つかりません" in html


def test_paper_detail_links_to_neighborhood_graph(client: FlaskClient, papers) -> None:
    html = _html(client, f"/papers/{papers['b']}")

    assert f'href="/papers/{papers["b"]}/graph"' in html
    assert "周辺グラフを見る" in html


def test_unknown_paper_is_404(client: FlaskClient, papers) -> None:
    html = _html(client, f"/papers/{uuid.uuid4()}", status=404)

    assert "論文が見つかりません" in html
    assert client.get("/papers/not-a-uuid").status_code == 404


def test_library_form_has_csrf_token(client: FlaskClient, papers) -> None:
    html = _html(client, f"/papers/{papers['b']}")

    assert '<input type="hidden" name="csrf_token" value="' in html


def test_library_post_without_csrf_token_is_rejected(
    client: FlaskClient, papers, db_path: Path
) -> None:
    response = client.post(f"/papers/{papers['b']}/library", data={"action": "add"})

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "フォームを送信できませんでした" in html
    assert "Traceback" not in html
    with open_database(db_path) as engine:
        assert get_paper(engine, papers["b"]).in_library is False


def test_library_post_with_invalid_csrf_token_is_rejected(
    client: FlaskClient, papers, db_path: Path
) -> None:
    response = client.post(
        f"/papers/{papers['b']}/library",
        data={"action": "add", "csrf_token": "not-a-real-token"},
    )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "フォームを送信できませんでした" in html
    assert "Traceback" not in html
    with open_database(db_path) as engine:
        assert get_paper(engine, papers["b"]).in_library is False


def test_library_add_and_remove_with_csrf_token(
    client: FlaskClient, papers, db_path: Path
) -> None:
    url = f"/papers/{papers['b']}/library"
    token = _csrf_token(_html(client, f"/papers/{papers['b']}"))

    added = client.post(url, data={"action": "add", "csrf_token": token})
    with open_database(db_path) as engine:
        assert get_paper(engine, papers["b"]).in_library is True
    token = _csrf_token(_html(client, f"/papers/{papers['b']}"))
    removed = client.post(url, data={"action": "remove", "csrf_token": token})
    with open_database(db_path) as engine:
        assert get_paper(engine, papers["b"]).in_library is False

    assert added.status_code == removed.status_code == 303
    assert added.headers["Location"].endswith(f"/papers/{papers['b']}")
    token = _csrf_token(_html(client, f"/papers/{papers['b']}"))
    invalid = client.post(url, data={"action": "delete", "csrf_token": token})
    assert invalid.status_code == 400


def test_library_post_for_unknown_paper_is_404(
    client: FlaskClient, papers, db_path: Path
) -> None:
    token = _csrf_token(_html(client, f"/papers/{papers['a']}"))
    response = client.post(
        f"/papers/{uuid.uuid4()}/library",
        data={"action": "add", "csrf_token": token},
    )

    assert response.status_code == 404


def test_uninitialized_database_is_503(tmp_path: Path) -> None:
    client = create_app(tmp_path / "missing.db").test_client()

    response = client.get("/papers")

    assert response.status_code == 503
    assert "llat init" in response.get_data(as_text=True)
    assert not (tmp_path / "missing.db").exists()


def test_web_reflects_changes_made_through_core(
    client: FlaskClient, papers, db_path: Path
) -> None:
    with open_database(db_path) as engine:
        add_to_library(engine, papers["c"])

    assert _html(client, "/papers").count("ライブラリ未登録") == 1


def test_usage_page_explains_concepts_and_commands(
    client: FlaskClient, db_path: Path
) -> None:
    html = _html(client, "/usage")

    assert "<h1>使い方</h1>" in html
    assert "論文Aが論文Bを引用している" in html
    assert 'llat --db "$DB" citation add 引用元ID 引用先ID' in html
    assert 'llat --db "$DB" path 始点ID 終点ID' in html
    assert f"DB={db_path}" in html
    assert 'href="/usage" aria-current="page"' in html


def test_usage_page_is_linked_from_navigation(client: FlaskClient, papers) -> None:
    assert 'href="/usage"' in _html(client, "/")


def test_usage_page_works_without_database(tmp_path: Path) -> None:
    client = create_app(tmp_path / "missing.db").test_client()

    assert client.get("/usage").status_code == 200
    assert not (tmp_path / "missing.db").exists()


def test_paper_detail_shows_year_and_documents(
    client: FlaskClient, db_path: Path, tmp_path: Path
) -> None:
    from pdf_factory import write_pdf

    from litlattice.documents import list_documents
    from litlattice.scan import scan_sources
    from litlattice.sources import add_source

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    write_pdf(pdf_dir / "2401.12345v1.pdf")
    with open_database(db_path) as engine:
        add_source(engine, pdf_dir)
        (identification,) = scan_sources(engine).identifications
        paper_id = identification.paper_id
        assert list_documents(engine, paper_id=paper_id)

    html = _html(client, f"/papers/{paper_id}")

    assert str(pdf_dir / "2401.12345v1.pdf") in html
    assert "出版年" not in html


def test_paper_detail_without_documents(client: FlaskClient, papers) -> None:
    html = _html(client, f"/papers/{papers['a']}")

    assert "<dt>PDF</dt>" in html
    assert "なし" in html
