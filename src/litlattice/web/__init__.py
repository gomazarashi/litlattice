"""Local web interface for LitLattice.

A primary interface (ADR 0004) over the same Core use cases as the CLI. It
listens on 127.0.0.1 and is not meant for external or production deployment.
"""

import os
import secrets
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy.engine import Engine
from werkzeug.exceptions import BadRequest, HTTPException

from litlattice.citations import list_all_citations, list_citations, list_references
from litlattice.database import open_database
from litlattice.documents import (
    inspect_document,
    link_document,
    list_documents,
    unlink_document,
)
from litlattice.errors import (
    DatabaseNotInitialized,
    DocumentNotFound,
    InvalidIdentifier,
    PaperNotFound,
    PaperNotIdentifiable,
    ProviderError,
    ProviderWorkNotFound,
    WorkNotFound,
)
from litlattice.expand import (
    ExpandDirection,
    MetadataResult,
    MetadataStatus,
    expand_citations,
    fetch_paper_metadata,
)
from litlattice.graph import GraphDirection, library_graph, neighborhood_graph
from litlattice.importing import (
    ImportResult,
    ImportStatus,
    import_paper,
    import_paper_for_document,
    search_works,
)
from litlattice.openalex import OpenAlexClient
from litlattice.papers import (
    Identifier,
    PaperRecord,
    add_to_library,
    get_paper,
    list_papers,
    remove_from_library,
)
from litlattice.paths import resolve_db_path
from litlattice.providers import PaperProvider
from litlattice.serialization import error_envelope, failure_envelope, success_envelope

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

_PREFERRED_SCHEMES = ("doi", "arxiv")
_API_DEPTHS = (0, 1, 2)
_PAGE_DEPTHS = (1, 2)
_IMPORT_SCHEMES = ("doi", "arxiv", "openalex")
_EXPAND_MAX_NODES = (20, 50, 100)


def primary_identifier(paper: PaperRecord) -> str | None:
    """The identifier to show when there is room for only one."""
    by_scheme = {i.scheme: i for i in reversed(paper.identifiers)}
    for scheme in _PREFERRED_SCHEMES:
        if scheme in by_scheme:
            identifier = by_scheme[scheme]
            return f"{identifier.scheme}:{identifier.normalized_value}"
    if paper.identifiers:
        first = paper.identifiers[0]
        return f"{first.scheme}:{first.normalized_value}"
    return None


def paper_label(paper: PaperRecord) -> str:
    """Title, else primary identifier, else a shortened UUID."""
    return paper.title or primary_identifier(paper) or str(paper.id)[:8]


def _parse_depth(raw: str, allowed: tuple[int, ...]) -> int | None:
    try:
        depth = int(raw)
    except ValueError:
        return None
    return depth if depth in allowed else None


def _parse_direction(raw: str) -> GraphDirection | None:
    try:
        return GraphDirection(raw)
    except ValueError:
        return None


def _parse_expand_direction(raw: str) -> ExpandDirection | None:
    try:
        return ExpandDirection(raw)
    except ValueError:
        return None


def _parse_max_nodes(raw: str) -> int | None:
    try:
        max_nodes = int(raw)
    except ValueError:
        return None
    return max_nodes if max_nodes in _EXPAND_MAX_NODES else None


def _parse_paper_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        abort(400, description="論文 ID は UUID で指定してください。")


def _format_identifiers(identifiers: tuple[Identifier, ...]) -> str:
    return " ".join(f"{i.scheme}:{i.normalized_value}" for i in identifiers)


def _flash_provider_error(error: ProviderError) -> None:
    flash(f"OpenAlex と通信できませんでした: {error}", "error")


def _flash_import_result(result: ImportResult) -> None:
    if result.status is ImportStatus.created:
        flash("論文を取り込みました。ライブラリには追加していません。", "success")
    elif result.status is ImportStatus.matched:
        flash("既存の論文に一致しました。", "success")
        if result.identifiers_added:
            flash(
                "追加した識別子: " + _format_identifiers(result.identifiers_added),
                "success",
            )
    elif result.status is ImportStatus.ambiguous:
        flash(
            "これらの識別子は複数の既存論文に属するため、何も変更していません。",
            "warning",
        )
    else:
        flash(
            "OpenAlex の記録が指定した識別子を含まないため、"
            "同じ論文と確認できませんでした。何も保存していません。",
            "warning",
        )
    if result.identifiers_ignored:
        flash(
            "OpenAlex の記録が報告していないため追加していません"
            "（別バージョンの論文の可能性があります）: "
            + _format_identifiers(result.identifiers_ignored),
            "warning",
        )


def _flash_metadata_result(result: MetadataResult) -> None:
    if result.status is MetadataStatus.updated:
        filled = []
        if result.title_set:
            filled.append("タイトル")
        if result.publication_year_set:
            filled.append("出版年")
        if filled:
            flash(
                "OpenAlex から metadata を取得しました（"
                + "・".join(filled)
                + "を補いました）。",
                "success",
            )
        else:
            flash(
                "OpenAlex から metadata を取得しました（新しく補った項目はありません）。",
                "success",
            )
        if result.identifiers_added:
            flash(
                "追加した識別子: " + _format_identifiers(result.identifiers_added),
                "success",
            )
    elif result.status is MetadataStatus.unconfirmed:
        flash(
            "OpenAlex の記録がこの論文と識別子を共有していないため、"
            "同じ論文と確認できませんでした。metadata は適用していません。",
            "warning",
        )
    else:
        flash(
            "OpenAlex の記録は別の論文にも一致するため、何も変更していません。",
            "warning",
        )


def create_app(
    db_path: str | os.PathLike[str] | None = None,
    *,
    secret_key: str | None = None,
    provider_factory: Callable[[], PaperProvider] | None = None,
) -> Flask:
    resolved_db = resolve_db_path(db_path)
    make_provider = provider_factory or OpenAlexClient.from_environment
    app = Flask(__name__)
    app.secret_key = secret_key or secrets.token_hex(32)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    CSRFProtect(app)
    app.jinja_env.globals.update(
        paper_label=paper_label,
        primary_identifier=primary_identifier,
        db_path=resolved_db,
    )

    @contextmanager
    def database() -> Iterator[Engine]:
        with open_database(resolved_db) as engine:
            yield engine

    def wants_json() -> bool:
        return request.path.startswith("/api/")

    def json_failure(error_type: str, message: str, status: int):
        return jsonify(failure_envelope(error_type, message)), status

    @app.errorhandler(PaperNotFound)
    def paper_not_found(error: PaperNotFound):
        if wants_json():
            return jsonify(error_envelope(error)), 404
        return (
            render_template(
                "error.html",
                title="論文が見つかりません",
                message="指定された ID の論文はデータベースに存在しません。",
                detail=str(error.paper_id),
            ),
            404,
        )

    @app.errorhandler(DocumentNotFound)
    def document_not_found(error: DocumentNotFound):
        if wants_json():
            return jsonify(error_envelope(error)), 404
        return (
            render_template(
                "error.html",
                title="PDF が見つかりません",
                message="指定された ID の PDF（DocumentCopy）はデータベースに存在しません。",
                detail=str(error.document_id),
            ),
            404,
        )

    @app.errorhandler(DatabaseNotInitialized)
    def database_not_initialized(error: DatabaseNotInitialized):
        if wants_json():
            return jsonify(error_envelope(error)), 503
        return (
            render_template(
                "error.html",
                title="データベースを利用できません",
                message="データベースが初期化されていないか、schema が対応していません。"
                "`llat init` で初期化してください。以前の開発版DBは新しいパスで作り直してください。",
                detail=str(error.db_path),
            ),
            503,
        )

    @app.errorhandler(400)
    def bad_request(error):
        if wants_json():
            return json_failure(
                type(error).__name__, error.description or "Bad Request", 400
            )
        return (
            render_template(
                "error.html",
                title="リクエストが正しくありません",
                message=error.description
                if error.description != BadRequest.description
                else "URL や送信内容を確認してください。",
                detail=None,
            ),
            400,
        )

    @app.errorhandler(CSRFError)
    def csrf_error(error: CSRFError):
        return (
            render_template(
                "error.html",
                title="フォームを送信できませんでした",
                message="ページの有効期限が切れたか、送信内容を確認できませんでした。"
                "ページを再読み込みしてからやり直してください。",
                detail=None,
            ),
            400,
        )

    @app.errorhandler(404)
    def not_found(error):
        if wants_json():
            return json_failure(type(error).__name__, error.description, 404)
        return (
            render_template(
                "error.html",
                title="ページが見つかりません",
                message="URL を確認してください。",
                detail=None,
            ),
            404,
        )

    @app.errorhandler(HTTPException)
    def http_exception(error: HTTPException):
        if wants_json():
            return json_failure(
                type(error).__name__,
                error.description or error.name,
                error.code or 500,
            )
        return error.get_response()

    @app.get("/")
    def dashboard():
        with database() as engine:
            papers = list_papers(engine)
            edges = list_all_citations(engine)
        cited_counts = Counter(e.cited_paper_id for e in edges)
        by_id = {p.id: p for p in papers}
        most_cited = [
            (by_id[paper_id], count)
            for paper_id, count in sorted(
                cited_counts.items(), key=lambda item: (-item[1], str(item[0]))
            )[:5]
        ]
        return render_template(
            "dashboard.html",
            paper_count=len(papers),
            library_count=sum(p.in_library for p in papers),
            citation_count=len(edges),
            most_cited=most_cited,
        )

    @app.get("/papers")
    def papers():
        with database() as engine:
            papers = list_papers(engine)
            edges = list_all_citations(engine)
        return render_template(
            "papers.html",
            papers=papers,
            reference_counts=Counter(e.citing_paper_id for e in edges),
            citation_counts=Counter(e.cited_paper_id for e in edges),
        )

    @app.get("/papers/<uuid:paper_id>")
    def paper_detail(paper_id: uuid.UUID):
        with database() as engine:
            paper = get_paper(engine, paper_id)
            references = list_references(engine, paper_id)
            cited_by = list_citations(engine, paper_id)
            documents = list_documents(engine, paper_id=paper_id)
        return render_template(
            "paper.html",
            paper=paper,
            references=references,
            cited_by=cited_by,
            documents=documents,
        )

    @app.get("/documents")
    def documents():
        unlinked = request.args.get("unlinked") == "1"
        with database() as engine:
            records = list_documents(engine, unlinked_only=unlinked)
            papers = {paper.id: paper for paper in list_papers(engine)}
        return render_template(
            "documents.html", documents=records, papers=papers, unlinked=unlinked
        )

    @app.get("/documents/<uuid:document_id>")
    def document_detail(document_id: uuid.UUID):
        with database() as engine:
            inspection = inspect_document(engine, document_id)
            linked_paper = (
                get_paper(engine, inspection.document.paper_id)
                if inspection.document.paper_id is not None
                else None
            )
        return render_template(
            "document.html",
            inspection=inspection,
            linked_paper=linked_paper,
            candidates_by_id={c.id: c for c in inspection.candidates},
            importable_hints=tuple(
                hint
                for hint in inspection.hints
                if hint.paper_id is None and hint.scheme in _PREFERRED_SCHEMES
            ),
        )

    @app.post("/documents/<uuid:document_id>/link")
    def document_link(document_id: uuid.UUID):
        paper_id = _parse_paper_id(request.form.get("paper_id", ""))
        with database() as engine:
            created = link_document(engine, document_id, paper_id)
        if created:
            flash(
                "PDF を論文に関連付けました。ライブラリには追加していません。",
                "success",
            )
        else:
            flash("この PDF は既にその論文に関連付けられています。", "warning")
        return redirect(url_for("document_detail", document_id=document_id), code=303)

    @app.post("/documents/<uuid:document_id>/unlink")
    def document_unlink(document_id: uuid.UUID):
        paper_id = _parse_paper_id(request.form.get("paper_id", ""))
        with database() as engine:
            removed = unlink_document(engine, document_id, paper_id)
        if removed:
            flash(
                "論文との関連付けを解除しました。PDF と論文は残っています。", "success"
            )
        else:
            flash("この PDF はその論文に関連付けられていません。", "warning")
        return redirect(url_for("document_detail", document_id=document_id), code=303)

    @app.post("/documents/<uuid:document_id>/import")
    def document_import(document_id: uuid.UUID):
        scheme = request.form.get("scheme", "")
        value = request.form.get("value", "").strip()
        if scheme not in _IMPORT_SCHEMES or not value:
            with database() as engine:
                inspect_document(engine, document_id)
            flash("識別子の種類と値を入力してください。", "error")
            return redirect(
                url_for("document_detail", document_id=document_id), code=303
            )
        try:
            with database() as engine:
                result = import_paper_for_document(
                    engine, make_provider(), document_id, [(scheme, value)]
                )
        except ProviderError as error:
            _flash_provider_error(error)
        except WorkNotFound as error:
            flash(f"OpenAlex に該当する論文がありませんでした: {error}", "error")
        except InvalidIdentifier as error:
            flash(str(error), "error")
        else:
            _flash_import_result(result.import_result)
            if result.linked:
                flash(
                    "PDF を論文に関連付けました。ライブラリには追加していません。",
                    "success",
                )
            elif result.import_result.status in (
                ImportStatus.created,
                ImportStatus.matched,
            ):
                flash("この PDF は既にその論文に関連付けられています。", "warning")
        return redirect(url_for("document_detail", document_id=document_id), code=303)

    @app.get("/papers/new")
    def paper_add():
        query = (request.args.get("q") or "").strip()
        result = None
        if query:
            try:
                with database() as engine:
                    result = search_works(engine, make_provider(), query, limit=10)
            except ProviderError as error:
                _flash_provider_error(error)
                return redirect(url_for("paper_add"), code=303)
        return render_template("paper_add.html", query=query, result=result)

    @app.post("/papers/import")
    def paper_import():
        scheme = request.form.get("scheme", "")
        value = request.form.get("value", "").strip()
        if scheme not in _IMPORT_SCHEMES or not value:
            flash("識別子の種類と値を入力してください。", "error")
            return redirect(url_for("paper_add"), code=303)
        try:
            with database() as engine:
                result = import_paper(engine, make_provider(), [(scheme, value)])
        except ProviderError as error:
            _flash_provider_error(error)
            return redirect(url_for("paper_add"), code=303)
        except WorkNotFound as error:
            flash(f"OpenAlex に該当する論文がありませんでした: {error}", "error")
            return redirect(url_for("paper_add"), code=303)
        except InvalidIdentifier as error:
            flash(str(error), "error")
            return redirect(url_for("paper_add"), code=303)
        _flash_import_result(result)
        if result.status in (ImportStatus.created, ImportStatus.matched):
            return redirect(url_for("paper_detail", paper_id=result.paper_id), code=303)
        return redirect(url_for("paper_add"), code=303)

    @app.post("/papers/<uuid:paper_id>/fetch")
    def paper_fetch(paper_id: uuid.UUID):
        try:
            with database() as engine:
                result = fetch_paper_metadata(engine, make_provider(), paper_id)
        except ProviderError as error:
            _flash_provider_error(error)
        except PaperNotIdentifiable:
            flash("外部識別子がないため OpenAlex で探せません。", "error")
        except ProviderWorkNotFound:
            flash("OpenAlex にこの論文の記録がありませんでした。", "error")
        else:
            _flash_metadata_result(result)
        return redirect(url_for("paper_detail", paper_id=paper_id), code=303)

    @app.post("/papers/<uuid:paper_id>/expand")
    def paper_expand(paper_id: uuid.UUID):
        depth = _parse_depth(request.form.get("depth", ""), _PAGE_DEPTHS)
        direction = _parse_expand_direction(request.form.get("direction", ""))
        max_nodes = _parse_max_nodes(request.form.get("max_nodes", ""))
        if depth is None or direction is None or max_nodes is None:
            abort(
                400,
                description="深さは 1 または 2、方向は references / citations / both、"
                "上限は 20 / 50 / 100 のいずれかを指定してください。",
            )
        try:
            with database() as engine:
                result = expand_citations(
                    engine,
                    make_provider(),
                    paper_id,
                    direction=direction,
                    depth=depth,
                    max_nodes=max_nodes,
                )
        except ProviderError as error:
            _flash_provider_error(error)
            return redirect(url_for("paper_detail", paper_id=paper_id), code=303)
        except PaperNotIdentifiable:
            flash("外部識別子がないため OpenAlex で探せません。", "error")
            return redirect(url_for("paper_detail", paper_id=paper_id), code=303)
        except ProviderWorkNotFound:
            flash("OpenAlex にこの論文の記録がありませんでした。", "error")
            return redirect(url_for("paper_detail", paper_id=paper_id), code=303)
        if result.seed_metadata.status is MetadataStatus.updated:
            flash(
                f"論文 {len(result.papers_created)} 件を新たに保存し、"
                f"引用 {result.citations_created} 件を追加しました"
                f"（既存 {len(result.papers_matched)} 件・"
                f"確認 {result.citations_confirmed} 件）。"
                "ライブラリには追加していません。",
                "success",
            )
            if result.truncated:
                flash(f"上限（{max_nodes} 件）で探索を打ち切りました。", "warning")
            return redirect(
                url_for(
                    "paper_graph",
                    paper_id=paper_id,
                    depth=depth,
                    direction=direction.value,
                ),
                code=303,
            )
        if result.seed_metadata.status is MetadataStatus.unconfirmed:
            flash(
                "OpenAlex の記録がこの論文と識別子を共有していないため、"
                "同じ論文と確認できませんでした。何も保存していません。",
                "warning",
            )
        else:
            flash(
                "OpenAlex の記録は別の論文にも一致するため、何も保存していません。",
                "warning",
            )
        return redirect(url_for("paper_detail", paper_id=paper_id), code=303)

    @app.post("/papers/<uuid:paper_id>/library")
    def change_library(paper_id: uuid.UUID):
        action = request.form.get("action")
        if action not in ("add", "remove"):
            abort(400)
        with database() as engine:
            if action == "add":
                add_to_library(engine, paper_id)
            else:
                remove_from_library(engine, paper_id)
        return redirect(url_for("paper_detail", paper_id=paper_id), code=303)

    @app.get("/usage")
    def usage():
        return render_template("usage.html")

    @app.get("/graph")
    def graph():
        with database() as engine:
            graph = library_graph(engine)
        return render_template(
            "graph.html",
            graph=graph,
            api_url=url_for("api_library_graph"),
        )

    @app.get("/papers/<uuid:paper_id>/graph")
    def paper_graph(paper_id: uuid.UUID):
        depth = _parse_depth(request.args.get("depth", "1"), _PAGE_DEPTHS)
        direction = _parse_direction(request.args.get("direction", "both"))
        if depth is None or direction is None:
            abort(
                400,
                description="深さは 1 または 2、方向は references / citations / both "
                "のいずれかを指定してください。",
            )
        with database() as engine:
            graph = neighborhood_graph(
                engine, paper_id, depth=depth, direction=direction
            )
        return render_template(
            "neighborhood.html",
            graph=graph,
            seed=graph.nodes[0].paper,
            depth=depth,
            direction=direction,
            depths=_PAGE_DEPTHS,
            directions=tuple(GraphDirection),
            api_url=url_for(
                "api_neighborhood_graph",
                paper_id=paper_id,
                depth=depth,
                direction=direction.value,
            ),
        )

    @app.get("/api/graph/library")
    def api_library_graph():
        with database() as engine:
            graph = library_graph(engine)
        return jsonify(success_envelope(graph))

    @app.get("/api/graph/neighborhood/<uuid:paper_id>")
    def api_neighborhood_graph(paper_id: uuid.UUID):
        depth = _parse_depth(request.args.get("depth", "1"), _API_DEPTHS)
        if depth is None:
            return json_failure("InvalidParameter", "depth must be 0, 1 or 2", 400)
        direction = _parse_direction(request.args.get("direction", "both"))
        if direction is None:
            return json_failure(
                "InvalidParameter",
                "direction must be references, citations or both",
                400,
            )
        with database() as engine:
            graph = neighborhood_graph(
                engine, paper_id, depth=depth, direction=direction
            )
        return jsonify(success_envelope(graph))

    return app


def serve(
    db_path: str | os.PathLike[str] | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Run Flask's built-in development server (local use only)."""
    resolved_db = resolve_db_path(db_path)
    with open_database(resolved_db):
        pass  # Fail fast with a clear error before starting the server.
    create_app(resolved_db).run(host=host, port=port, debug=False)
