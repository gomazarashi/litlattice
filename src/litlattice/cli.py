"""Command-line interface for LitLattice."""

import json
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any

import typer
from sqlalchemy.engine import Engine

from litlattice.citations import (
    add_citation,
    find_citation_path,
    list_citations,
    list_references,
)
from litlattice.database import open_database
from litlattice.documents import (
    DocumentIdentification,
    DocumentInspection,
    IdentificationStatus,
    identify_documents,
    inspect_document,
    link_document,
    list_documents,
    unlink_document,
)
from litlattice.errors import LitLatticeError
from litlattice.expand import (
    DEFAULT_MAX_NODES,
    ExpandDirection,
    MetadataStatus,
    expand_citations,
    fetch_paper_metadata,
)
from litlattice.graph import (
    CitationGraph,
    GraphDirection,
    GraphKind,
    library_graph,
    neighborhood_graph,
)
from litlattice.identifiers import ARXIV, DOI
from litlattice.importing import (
    CandidateStatus,
    ImportResult,
    ImportStatus,
    import_paper,
    import_paper_for_document,
    search_works,
)
from litlattice.installation import initialize
from litlattice.openalex import OpenAlexClient
from litlattice.papers import (
    PaperRecord,
    add_to_library,
    create_paper,
    get_paper,
    list_papers,
    remove_from_library,
)
from litlattice.scan import scan_sources
from litlattice.serialization import error_envelope, success_envelope
from litlattice.sources import (
    add_source,
    list_sources,
    remove_source,
    remove_source_by_path,
)

app = typer.Typer(
    help="LitLattice: organize research papers and explore citation graphs.",
    no_args_is_help=True,
)

paper_app = typer.Typer(no_args_is_help=True, help="Create and inspect Papers.")
lib_app = typer.Typer(no_args_is_help=True, help="Manage the Library.")
citation_app = typer.Typer(
    no_args_is_help=True, help="Record and list citations (A -> B: A cites B)."
)
src_app = typer.Typer(
    no_args_is_help=True,
    help="Manage Sources: directories that `scan` searches for PDFs.",
)
doc_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect document copies (PDF files) and their links to Papers.",
)
graph_app = typer.Typer(
    no_args_is_help=True,
    help="Analyze the citation graph stored in the database (A -> B: A cites B). Read-only.",
)

_UNCONFIRMED_NOTE = (
    "OpenAlex's record shares no identifier with this Paper, so it cannot be "
    "confirmed to be the same work; its metadata was not applied."
)
_EXPAND_UNCONFIRMED_NOTE = (
    "OpenAlex's record shares no identifier with this Paper, so it cannot be "
    "confirmed to be the same work; nothing was stored."
)
_CONFLICT_NOTE = "OpenAlex's record also matches another Paper; nothing was changed."
_IMPORT_AMBIGUOUS_NOTE = (
    "These identifiers belong to several existing Papers; nothing was changed."
)
_IMPORT_UNCONFIRMED_NOTE = (
    "OpenAlex's record shares no identifier with the given ones, so it cannot be "
    "confirmed to be the same work; nothing was stored."
)
_IMPORT_IGNORED_NOTE = (
    "Not attached because OpenAlex's record does not report them "
    "(they may denote another version of the paper)"
)


class Direction(StrEnum):
    references = "references"
    citations = "citations"
    both = "both"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"litlattice {version('litlattice')}")
        raise typer.Exit()


def _provider() -> OpenAlexClient:
    """The external provider used by ``paper fetch`` and ``expand``.

    Kept as a module-level function so tests can replace it without touching
    OpenAlex.
    """
    return OpenAlexClient.from_environment()


def _emit_json(data: Any, warnings: Sequence[str] = ()) -> None:
    typer.echo(json.dumps(success_envelope(data, warnings)))


def _emit_json_error(error: LitLatticeError) -> None:
    typer.echo(json.dumps(error_envelope(error)))


@contextmanager
def _handle_errors(json_mode: bool = False) -> Iterator[None]:
    try:
        yield
    except LitLatticeError as e:
        if json_mode:
            _emit_json_error(e)
        else:
            typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


@contextmanager
def _open_database(ctx: typer.Context, *, json_mode: bool = False) -> Iterator[Engine]:
    with _handle_errors(json_mode), open_database(ctx.obj) as engine:
        yield engine


@app.callback()
def root(
    ctx: typer.Context,
    show_version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="Database file. Defaults to $LITLATTICE_DB, then the XDG data dir.",
        ),
    ] = None,
) -> None:
    """LitLattice: organize research papers and explore citation graphs."""
    ctx.obj = db


def _label(record: PaperRecord) -> str:
    identifiers = " ".join(
        f"{identifier.scheme}:{identifier.normalized_value}"
        for identifier in record.identifiers
    )
    return "  ".join(part for part in (record.title, identifiers) if part)


def _print_paper(record: PaperRecord) -> None:
    typer.echo(f"ID:          {record.id}")
    typer.echo(f"Title:       {record.title or '(untitled)'}")
    if record.publication_year is not None:
        typer.echo(f"Year:        {record.publication_year}")
    typer.echo(f"Created:     {record.created_at.isoformat()}")
    typer.echo(f"In library:  {'yes' if record.in_library else 'no'}")
    if not record.identifiers:
        typer.echo("Identifiers: (none)")
        return
    typer.echo("Identifiers:")
    for identifier in record.identifiers:
        typer.echo(f"  {identifier.scheme}:{identifier.normalized_value}")


def _print_identification(identification: DocumentIdentification) -> None:
    if identification.status is IdentificationStatus.linked:
        typer.echo(f"  linked      {identification.path} -> {identification.paper_id}")
    elif identification.status is IdentificationStatus.created:
        typer.echo(f"  new paper   {identification.path} -> {identification.paper_id}")
    elif identification.status is IdentificationStatus.ambiguous:
        identifiers = " ".join(
            f"{identifier.scheme}:{identifier.normalized_value}"
            for identifier in identification.identifiers
        )
        typer.echo(f"  ambiguous   {identification.path}  ({identifiers})")
        for candidate in identification.candidate_paper_ids:
            typer.echo(f"      candidate {candidate}")
    else:
        typer.echo(f"  unidentified {identification.path}")


def _import_warnings(result: ImportResult) -> list[str]:
    """Warnings for an import result, shared by ``paper import`` and ``doc import``."""
    warnings: list[str] = []
    if result.status is ImportStatus.ambiguous:
        warnings.append(_IMPORT_AMBIGUOUS_NOTE)
    elif result.status is ImportStatus.unconfirmed:
        warnings.append(_IMPORT_UNCONFIRMED_NOTE)
    if result.identifiers_ignored:
        ignored = " ".join(
            f"{identifier.scheme}:{identifier.normalized_value}"
            for identifier in result.identifiers_ignored
        )
        warnings.append(f"{_IMPORT_IGNORED_NOTE}: {ignored}")
    return warnings


def _print_import_result(result: ImportResult, record: PaperRecord | None) -> None:
    """Print an import's outcome, without warnings or the Library note."""
    if result.status in (ImportStatus.created, ImportStatus.matched):
        if result.status is ImportStatus.created:
            typer.echo("Imported a new Paper.")
        else:
            typer.echo("Matched an existing Paper.")
            if result.identifiers_added:
                added = " ".join(
                    f"{identifier.scheme}:{identifier.normalized_value}"
                    for identifier in result.identifiers_added
                )
                typer.echo(f"Added identifiers: {added}")
        if record is not None:
            _print_paper(record)
    elif result.status is ImportStatus.ambiguous:
        typer.echo("Candidates:")
        for candidate in result.candidate_paper_ids:
            typer.echo(f"  {candidate}")


def _library_note(result: ImportResult, record: PaperRecord | None) -> str | None:
    """The Library reminder for an import, or ``None`` when there is none."""
    if record is None:
        return None
    if result.status is ImportStatus.created or (
        result.status is ImportStatus.matched and not record.in_library
    ):
        return f"Not added to the Library. Add it with `llat lib add {record.id}`."
    return None


@app.command()
def init(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Create or upgrade the local database (back up existing data first)."""
    with _handle_errors(json_mode=json_output):
        result = initialize(ctx.obj)
    if json_output:
        _emit_json(result)
        return
    typer.echo(f"Database: {result.db_path}")


@app.command()
def show(
    ctx: typer.Context,
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Paper ID (UUID).")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Show one Paper by ID."""
    with _open_database(ctx, json_mode=json_output) as engine:
        record = get_paper(engine, paper_id)
    if json_output:
        _emit_json(record)
        return
    _print_paper(record)


@app.command()
def search(
    ctx: typer.Context,
    query: Annotated[
        str, typer.Argument(help="Free text to search for, e.g. a paper title.")
    ],
    limit: Annotated[
        int,
        typer.Option(
            "--limit", "-n", min=1, max=50, help="How many candidates to show."
        ),
    ] = 10,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Search OpenAlex for works matching QUERY (e.g. a title). Shows candidates only; nothing is stored."""
    query = query.strip()
    if not query:
        raise typer.BadParameter("QUERY must not be empty.")
    if not json_output:
        typer.echo("Searching OpenAlex...", err=True)
    with _open_database(ctx, json_mode=json_output) as engine:
        result = search_works(engine, _provider(), query, limit=limit)
    if json_output:
        _emit_json(result)
        return
    if not result.candidates:
        typer.echo("No works found.")
        return
    for candidate in result.candidates:
        title = candidate.title or "(untitled)"
        if candidate.publication_year is not None:
            title = f"{title} ({candidate.publication_year})"
        identifiers = " ".join(
            f"{identifier.scheme}:{identifier.normalized_value}"
            for identifier in candidate.identifiers
        )
        line = f"{candidate.status.value:<9} {title}  {identifiers}"
        if candidate.status is CandidateStatus.existing:
            line += f"  -> {candidate.paper_id}"
        elif candidate.status is CandidateStatus.ambiguous:
            candidates = ", ".join(
                str(paper_id) for paper_id in candidate.candidate_paper_ids
            )
            line += f"  -> {candidates}"
        typer.echo(line.rstrip())
    typer.echo(
        "Import one with `llat paper import --openalex <ID>` "
        "(nothing is stored until you do)."
    )


@paper_app.command("create")
def paper_create(
    ctx: typer.Context,
    doi: Annotated[
        list[str] | None, typer.Option("--doi", help="DOI; repeatable.")
    ] = None,
    arxiv: Annotated[
        list[str] | None, typer.Option("--arxiv", help="arXiv ID; repeatable.")
    ] = None,
    title: Annotated[
        str | None, typer.Option("--title", help="Title (metadata, not identity).")
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Create a Paper from DOI and/or arXiv identifiers."""
    identifiers = [("doi", value) for value in doi or []]
    identifiers += [("arxiv", value) for value in arxiv or []]
    with _open_database(ctx, json_mode=json_output) as engine:
        record = create_paper(engine, identifiers, title=title)
    if json_output:
        _emit_json(record)
        return
    _print_paper(record)


@paper_app.command("import")
def paper_import(
    ctx: typer.Context,
    doi: Annotated[
        list[str] | None, typer.Option("--doi", help="DOI; repeatable.")
    ] = None,
    arxiv: Annotated[
        list[str] | None, typer.Option("--arxiv", help="arXiv ID; repeatable.")
    ] = None,
    openalex: Annotated[
        list[str] | None,
        typer.Option("--openalex", help="OpenAlex work ID; repeatable."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Import a Paper from OpenAlex by its DOI, arXiv ID or OpenAlex ID. Does not add it to the Library."""
    identifiers = [("doi", value) for value in doi or []]
    identifiers += [("arxiv", value) for value in arxiv or []]
    identifiers += [("openalex", value) for value in openalex or []]
    if not identifiers:
        raise typer.BadParameter("Give at least one of --doi, --arxiv or --openalex.")
    if not json_output:
        typer.echo("Fetching from OpenAlex...", err=True)
    with _open_database(ctx, json_mode=json_output) as engine:
        result = import_paper(engine, _provider(), identifiers)
        record = (
            get_paper(engine, result.paper_id) if result.paper_id is not None else None
        )
    warnings: list[str] = _import_warnings(result)
    if json_output:
        _emit_json(result, warnings)
        return
    for warning in warnings:
        typer.echo(warning, err=True)
    _print_import_result(result, record)
    note = _library_note(result, record)
    if note is not None:
        typer.echo(note)


@paper_app.command("fetch")
def paper_fetch(
    ctx: typer.Context,
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Paper ID (UUID).")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Fill missing metadata of a Paper from OpenAlex. Does not add it to the Library."""
    if not json_output:
        typer.echo("Fetching metadata from OpenAlex...", err=True)
    with _open_database(ctx, json_mode=json_output) as engine:
        result = fetch_paper_metadata(engine, _provider(), paper_id)
        record = get_paper(engine, paper_id)
    warnings: list[str] = []
    if result.status is MetadataStatus.unconfirmed:
        warnings.append(_UNCONFIRMED_NOTE)
    elif result.status is MetadataStatus.conflict:
        warnings.append(_CONFLICT_NOTE)
    if json_output:
        _emit_json(result, warnings)
        return
    if result.status is MetadataStatus.updated:
        typer.echo("Metadata updated from OpenAlex.")
        if result.identifiers_added:
            identifiers = " ".join(
                f"{identifier.scheme}:{identifier.normalized_value}"
                for identifier in result.identifiers_added
            )
            typer.echo(f"Added identifiers: {identifiers}")
    elif result.status is MetadataStatus.unconfirmed:
        typer.echo(_UNCONFIRMED_NOTE)
    else:
        typer.echo(f"Warning: {_CONFLICT_NOTE}", err=True)
    _print_paper(record)


@paper_app.command("list")
def paper_list(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """List every Paper, marking Library members with ``L``."""
    with _open_database(ctx, json_mode=json_output) as engine:
        records = list_papers(engine)
    if json_output:
        _emit_json(records)
        return
    for record in records:
        marker = "L" if record.in_library else "-"
        typer.echo(f"{record.id}  {marker}  {_label(record)}".rstrip())


@lib_app.command("add")
def lib_add(
    ctx: typer.Context,
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Paper ID (UUID).")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Add an existing Paper to the Library."""
    with _open_database(ctx, json_mode=json_output) as engine:
        added = add_to_library(engine, paper_id)
    if json_output:
        _emit_json({"paper_id": paper_id, "added": added})
        return
    if added:
        typer.echo(f"Added to library: {paper_id}")
    else:
        typer.echo(f"Already in library: {paper_id}")


@lib_app.command("remove")
def lib_remove(
    ctx: typer.Context,
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Paper ID (UUID).")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Remove a Paper from the Library. The Paper itself is kept."""
    with _open_database(ctx, json_mode=json_output) as engine:
        removed = remove_from_library(engine, paper_id)
    if json_output:
        _emit_json({"paper_id": paper_id, "removed": removed})
        return
    if removed:
        typer.echo(f"Removed from library: {paper_id}")
    else:
        typer.echo(f"Not in library: {paper_id}")


@citation_app.command("add")
def citation_add(
    ctx: typer.Context,
    citing_id: Annotated[uuid.UUID, typer.Argument(help="Citing Paper ID.")],
    cited_id: Annotated[uuid.UUID, typer.Argument(help="Cited Paper ID.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Record that CITING_ID cites CITED_ID."""
    with _open_database(ctx, json_mode=json_output) as engine:
        created = add_citation(engine, citing_id, cited_id)
    if json_output:
        _emit_json(
            {
                "citing_paper_id": citing_id,
                "cited_paper_id": cited_id,
                "created": created,
            }
        )
        return
    if created:
        typer.echo(f"Added citation: {citing_id} -> {cited_id}")
    else:
        typer.echo(f"Citation already recorded: {citing_id} -> {cited_id}")


@citation_app.command("list")
def citation_list(
    ctx: typer.Context,
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Paper ID (UUID).")],
    direction: Annotated[
        Direction,
        typer.Option(
            "--direction",
            help="references: papers this paper cites (->); "
            "citations: papers that cite this paper (<-).",
        ),
    ] = Direction.both,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """List papers linked to PAPER_ID by citations."""
    with _open_database(ctx, json_mode=json_output) as engine:
        references = (
            list_references(engine, paper_id)
            if direction in (Direction.references, Direction.both)
            else []
        )
        citations = (
            list_citations(engine, paper_id)
            if direction in (Direction.citations, Direction.both)
            else []
        )
    if json_output:
        _emit_json(
            {
                "paper_id": paper_id,
                "direction": direction,
                "references": references,
                "citations": citations,
            }
        )
        return
    for record in references:
        typer.echo(f"-> {record.id}  {_label(record)}".rstrip())
    for record in citations:
        typer.echo(f"<- {record.id}  {_label(record)}".rstrip())


def _print_graph(graph: CitationGraph) -> None:
    if graph.kind is GraphKind.neighborhood:
        typer.echo(
            f"Neighborhood of {graph.seed_paper_id} "
            f"(direction={graph.direction.value}, depth={graph.depth})"
        )
    else:
        typer.echo("Library graph")
    typer.echo(
        f"{len(graph.nodes)} papers, {len(graph.edges)} citations. "
        "Degrees and PageRank are computed within this graph."
    )
    if not graph.nodes:
        typer.echo("The Library is empty.")
        return
    typer.echo("Papers:")
    for node in graph.nodes:
        prefix = f"d{node.distance} " if graph.kind is GraphKind.neighborhood else ""
        line = (
            f"  {prefix}{node.paper.id}  in {node.in_degree}  "
            f"out {node.out_degree}  pagerank {node.pagerank:.4f}  "
            f"{_label(node.paper)}"
        )
        typer.echo(line.rstrip())
    typer.echo("Citations (A -> B: A cites B):")
    if not graph.edges:
        typer.echo("  (none)")
        return
    for edge in graph.edges:
        typer.echo(f"  {edge.citing_paper_id} -> {edge.cited_paper_id}")


@graph_app.command("neighborhood")
def graph_neighborhood(
    ctx: typer.Context,
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Seed Paper ID (UUID).")],
    depth: Annotated[
        int,
        typer.Option(
            "--depth",
            "-d",
            min=0,
            help="How many citation hops to follow from the Paper.",
        ),
    ] = 1,
    direction: Annotated[
        GraphDirection,
        typer.Option(
            "--direction",
            help="references: papers this Paper cites (->); "
            "citations: papers that cite this Paper (<-).",
        ),
    ] = GraphDirection.both,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Show the stored citation graph around a Paper. Does not contact providers or change anything."""
    with _open_database(ctx, json_mode=json_output) as engine:
        graph = neighborhood_graph(engine, paper_id, depth=depth, direction=direction)
    if json_output:
        _emit_json(graph)
        return
    _print_graph(graph)


@graph_app.command("library")
def graph_library(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Show the citation graph among Library Papers. Does not change anything."""
    with _open_database(ctx, json_mode=json_output) as engine:
        graph = library_graph(engine)
    if json_output:
        _emit_json(graph)
        return
    _print_graph(graph)


@src_app.command("add")
def src_add(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Directory to register as a Source.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Register a directory as a scan Source."""
    with _open_database(ctx, json_mode=json_output) as engine:
        source, created = add_source(engine, path)
    if json_output:
        _emit_json({"source": source, "created": created})
        return
    if created:
        typer.echo(f"Added source: {source.id}  {source.normalized_path}")
    else:
        typer.echo(f"Source already registered: {source.id}  {source.normalized_path}")


@src_app.command("list")
def src_list(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """List registered Sources."""
    with _open_database(ctx, json_mode=json_output) as engine:
        records = list_sources(engine)
    if json_output:
        _emit_json(records)
        return
    for record in records:
        last_scanned = (
            record.last_scanned_at.isoformat()
            if record.last_scanned_at is not None
            else "never"
        )
        typer.echo(
            f"{record.id}  {record.normalized_path}  last scanned: {last_scanned}"
        )


@src_app.command("remove")
def src_remove(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="Source ID (UUID) or path.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Unregister a Source. No files or DocumentCopies are deleted."""
    with _open_database(ctx, json_mode=json_output) as engine:
        try:
            source_id = uuid.UUID(source)
        except ValueError:
            record = remove_source_by_path(engine, source)
        else:
            record = remove_source(engine, source_id)
    if json_output:
        _emit_json(record)
        return
    typer.echo(f"Removed source: {record.id}  {record.normalized_path}")
    typer.echo("Document copies found under it are kept.")


@doc_app.command("list")
def doc_list(
    ctx: typer.Context,
    paper_id: Annotated[
        uuid.UUID | None,
        typer.Option("--paper", help="Only documents linked to this Paper."),
    ] = None,
    unlinked: Annotated[
        bool,
        typer.Option("--unlinked", help="Only documents without a linked Paper."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """List document copies."""
    with _open_database(ctx, json_mode=json_output) as engine:
        records = list_documents(engine, paper_id=paper_id, unlinked_only=unlinked)
    if json_output:
        _emit_json(records)
        return
    for record in records:
        state = "missing" if record.missing_since is not None else "present"
        line = f"{record.id}  {state}  {record.path}"
        if record.paper_id is not None:
            line += f"  -> {record.paper_id}"
        typer.echo(line)


def _print_next_steps(inspection: DocumentInspection) -> None:
    document_id = inspection.document.id
    steps = [
        f"llat doc link {document_id} {candidate.id}"
        for candidate in inspection.candidates
    ]
    steps += [
        f"llat doc import {document_id} --{hint.scheme} {hint.normalized_value}"
        for hint in inspection.hints
        if hint.paper_id is None and hint.scheme in (DOI, ARXIV)
    ]
    if not steps:
        steps = [
            'llat search "<title>"',
            f"llat doc import {document_id} --openalex <ID>",
        ]
    typer.echo("Next steps:")
    for step in steps:
        typer.echo(f"  {step}")


def _print_inspection(
    inspection: DocumentInspection, papers: dict[uuid.UUID, PaperRecord]
) -> None:
    document = inspection.document
    typer.echo(f"ID:         {document.id}")
    typer.echo(f"Path:       {document.path}")
    state = "missing" if document.missing_since is not None else "present"
    typer.echo(f"State:      {state}")
    if document.paper_id is None:
        typer.echo("Linked to:  (none)")
    else:
        label = _label(papers[document.paper_id])
        typer.echo(f"Linked to:  {document.paper_id}  {label}".rstrip())
    if not inspection.file_available:
        typer.echo("Hints:      (file not available)")
    else:
        typer.echo("Hints:")
        if not inspection.hints:
            typer.echo("  (none found)")
        for hint in inspection.hints:
            strength = "strong" if hint.strong else "weak  "
            target = str(hint.paper_id) if hint.paper_id is not None else "(no Paper)"
            typer.echo(
                f"  {hint.origin.value:<12} {strength} "
                f"{hint.scheme}:{hint.normalized_value}  -> {target}"
            )
    if inspection.assessment is not None:
        typer.echo(f"Assessment: {inspection.assessment.value}")
    if inspection.candidates:
        typer.echo("Candidates:")
        for candidate in inspection.candidates:
            typer.echo(f"  {candidate.id}  {_label(candidate)}".rstrip())
    if document.paper_id is None:
        _print_next_steps(inspection)


@doc_app.command("show")
def doc_show(
    ctx: typer.Context,
    document_id: Annotated[uuid.UUID, typer.Argument(help="Document copy ID (UUID).")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Show what a document copy's file says about its Paper: identifier hints, matching Papers and how to resolve it. Reads the file locally; nothing is changed."""
    with _open_database(ctx, json_mode=json_output) as engine:
        inspection = inspect_document(engine, document_id)
        papers = {candidate.id: candidate for candidate in inspection.candidates}
        paper_id = inspection.document.paper_id
        if paper_id is not None and paper_id not in papers:
            papers[paper_id] = get_paper(engine, paper_id)
    if json_output:
        _emit_json(inspection)
        return
    _print_inspection(inspection, papers)


@doc_app.command("link")
def doc_link(
    ctx: typer.Context,
    document_id: Annotated[uuid.UUID, typer.Argument(help="Document copy ID (UUID).")],
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Paper ID (UUID).")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Link a DocumentCopy to a Paper by hand. Does not download anything."""
    with _open_database(ctx, json_mode=json_output) as engine:
        created = link_document(engine, document_id, paper_id)
    if json_output:
        _emit_json(
            {"document_id": document_id, "paper_id": paper_id, "linked": created}
        )
        return
    if created:
        typer.echo(f"Linked: {document_id} -> {paper_id}")
    else:
        typer.echo(f"Already linked: {document_id} -> {paper_id}")


@doc_app.command("unlink")
def doc_unlink(
    ctx: typer.Context,
    document_id: Annotated[uuid.UUID, typer.Argument(help="Document copy ID (UUID).")],
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Paper ID (UUID).")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Remove the link between a DocumentCopy and a Paper. The file and the Paper are not deleted."""
    with _open_database(ctx, json_mode=json_output) as engine:
        removed = unlink_document(engine, document_id, paper_id)
    if json_output:
        _emit_json(
            {"document_id": document_id, "paper_id": paper_id, "unlinked": removed}
        )
        return
    if removed:
        typer.echo(f"Unlinked: {document_id} -> {paper_id}")
        typer.echo("The document copy, the Paper and the file are kept.")
    else:
        typer.echo(f"Not linked: {document_id} -> {paper_id}")


@doc_app.command("import")
def doc_import(
    ctx: typer.Context,
    document_id: Annotated[uuid.UUID, typer.Argument(help="Document copy ID (UUID).")],
    doi: Annotated[
        list[str] | None, typer.Option("--doi", help="DOI; repeatable.")
    ] = None,
    arxiv: Annotated[
        list[str] | None, typer.Option("--arxiv", help="arXiv ID; repeatable.")
    ] = None,
    openalex: Annotated[
        list[str] | None,
        typer.Option("--openalex", help="OpenAlex work ID; repeatable."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Import the Paper for a document copy from OpenAlex and link the document to it. Does not add it to the Library."""
    identifiers = [("doi", value) for value in doi or []]
    identifiers += [("arxiv", value) for value in arxiv or []]
    identifiers += [("openalex", value) for value in openalex or []]
    if not identifiers:
        raise typer.BadParameter("Give at least one of --doi, --arxiv or --openalex.")
    if not json_output:
        typer.echo("Fetching from OpenAlex...", err=True)
    with _open_database(ctx, json_mode=json_output) as engine:
        result = import_paper_for_document(
            engine, _provider(), document_id, identifiers
        )
        record = (
            get_paper(engine, result.import_result.paper_id)
            if result.import_result.paper_id is not None
            else None
        )
    warnings = _import_warnings(result.import_result)
    if json_output:
        _emit_json(result, warnings)
        return
    for warning in warnings:
        typer.echo(warning, err=True)
    _print_import_result(result.import_result, record)
    if result.linked:
        typer.echo(f"Linked: {document_id} -> {result.import_result.paper_id}")
    elif result.import_result.status in (ImportStatus.created, ImportStatus.matched):
        typer.echo(f"Already linked: {document_id} -> {result.import_result.paper_id}")
    note = _library_note(result.import_result, record)
    if note is not None:
        typer.echo(note)


@doc_app.command("identify")
def doc_identify(
    ctx: typer.Context,
    document_ids: Annotated[
        list[uuid.UUID] | None,
        typer.Argument(
            help="Document copy ID (UUID); repeatable. Defaults to every unlinked copy."
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Re-run Paper identification for unlinked document copies."""
    with _open_database(ctx, json_mode=json_output) as engine:
        identifications = identify_documents(engine, document_ids=document_ids)
    if json_output:
        _emit_json(identifications)
        return
    if not identifications:
        typer.echo("Nothing to identify.")
        return
    for identification in identifications:
        _print_identification(identification)


def _print_scan_identifications(
    identifications: Sequence[DocumentIdentification],
) -> None:
    if not identifications:
        return
    for identification in identifications:
        _print_identification(identification)
    identified = sum(
        1
        for identification in identifications
        if identification.status
        in (IdentificationStatus.linked, IdentificationStatus.created)
    )
    ambiguous = sum(
        1
        for identification in identifications
        if identification.status is IdentificationStatus.ambiguous
    )
    unidentified = len(identifications) - identified - ambiguous
    typer.echo(
        f"Identified: {identified} linked, {ambiguous} ambiguous, "
        f"{unidentified} unidentified."
    )


@app.command()
def scan(
    ctx: typer.Context,
    source_ids: Annotated[
        list[uuid.UUID] | None,
        typer.Option("--source", "-s", help="Source ID (UUID); repeatable."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Recognize PDFs under registered Sources as document copies. Does not add anything to the Library or download files."""
    with _open_database(ctx, json_mode=json_output) as engine:
        result = scan_sources(engine, source_ids=source_ids)
    if json_output:
        _emit_json(result)
        return
    if not result.sources:
        typer.echo("No sources registered. Add one with `llat src add PATH`.", err=True)
        return
    for source in result.sources:
        if source.available:
            typer.echo(
                f"{source.normalized_path}: {source.found} found, {source.new} new, "
                f"{source.changed} changed, {source.reappeared} reappeared, "
                f"{source.missing} missing"
            )
        else:
            typer.echo(
                f"{source.normalized_path}: unavailable "
                "(directory not found; existing copies left as is)"
            )
    _print_scan_identifications(result.identifications)
    typer.echo(f"Scanned {len(result.sources)} source(s).")


@app.command()
def expand(
    ctx: typer.Context,
    paper_id: Annotated[uuid.UUID, typer.Argument(help="Seed Paper ID (UUID).")],
    depth: Annotated[
        int,
        typer.Option("--depth", "-d", min=1, help="How many citation hops to follow."),
    ] = 1,
    direction: Annotated[
        ExpandDirection,
        typer.Option(
            "--direction",
            help="references: works this Paper cites (->); "
            "citations: works citing it (<-); both.",
        ),
    ] = ExpandDirection.both,
    max_nodes: Annotated[
        int,
        typer.Option(
            "--max-nodes",
            "-n",
            min=1,
            help="Stop after visiting this many works, including the seed.",
        ),
    ] = DEFAULT_MAX_NODES,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Discover citations around a Paper and store them. Does not add Papers to the Library or download files."""
    warnings: list[str] = []
    with _open_database(ctx, json_mode=json_output) as engine:
        if not json_output:
            typer.echo(
                "Expanding from OpenAlex "
                f"(direction={direction.value}, depth={depth}, "
                f"max nodes={max_nodes})...",
                err=True,
            )
        result = expand_citations(
            engine,
            _provider(),
            paper_id,
            direction=direction,
            depth=depth,
            max_nodes=max_nodes,
        )
    if result.truncated:
        warnings.append(
            f"Stopped at --max-nodes {max_nodes}; "
            "run again with a larger value to continue."
        )
    if result.seed_metadata.status is MetadataStatus.unconfirmed:
        warnings.append(_EXPAND_UNCONFIRMED_NOTE)
    elif result.seed_metadata.status is MetadataStatus.conflict:
        warnings.append(_CONFLICT_NOTE)
    if json_output:
        _emit_json(result, warnings)
        return
    typer.echo(f"Expanded {paper_id}")
    typer.echo(
        f"Papers: {len(result.papers_created)} new, "
        f"{len(result.papers_matched)} existing"
    )
    typer.echo(
        f"Citations: {result.citations_created} new, "
        f"{result.citations_confirmed} confirmed"
    )
    if result.skipped:
        typer.echo(f"Skipped {len(result.skipped)} work(s) matching several Papers:")
        for skipped in result.skipped:
            title = skipped.title or "(untitled)"
            identifiers = " ".join(
                f"{identifier.scheme}:{identifier.normalized_value}"
                for identifier in skipped.identifiers
            )
            typer.echo(f"  {title}  {identifiers}".rstrip())
    if result.truncated:
        typer.echo(warnings[0], err=True)
    if result.seed_metadata.status is MetadataStatus.unconfirmed:
        typer.echo(_EXPAND_UNCONFIRMED_NOTE, err=True)
    elif result.seed_metadata.status is MetadataStatus.conflict:
        typer.echo(f"Warning: {_CONFLICT_NOTE}", err=True)
    typer.echo(
        f"Not added to the Library. Review with `llat citation list {paper_id}` "
        "or the web UI."
    )


@app.command()
def serve(
    ctx: typer.Context,
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = 8000,
) -> None:
    """Start the local web UI on 127.0.0.1 (demo / personal use only)."""
    from litlattice.web import serve as serve_web

    with _handle_errors():
        serve_web(ctx.obj, port=port)


@app.command()
def path(
    ctx: typer.Context,
    source_id: Annotated[uuid.UUID, typer.Argument(help="Source Paper ID.")],
    target_id: Annotated[uuid.UUID, typer.Argument(help="Target Paper ID.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Print a JSON envelope instead of text."),
    ] = False,
) -> None:
    """Shortest path from SOURCE_ID to TARGET_ID following citation direction."""
    with _open_database(ctx, json_mode=json_output) as engine:
        records = find_citation_path(engine, source_id, target_id)
    hops = len(records) - 1
    if json_output:
        _emit_json(
            {
                "source_paper_id": source_id,
                "target_paper_id": target_id,
                "hops": hops,
                "papers": records,
            }
        )
        return
    typer.echo(f"Citation path ({hops} hop{'' if hops == 1 else 's'}):")
    for index, record in enumerate(records):
        prefix = "   " if index == 0 else "-> "
        typer.echo(f"{prefix}{record.id}  {_label(record)}".rstrip())


app.add_typer(paper_app, name="paper")
app.add_typer(lib_app, name="lib")
app.add_typer(citation_app, name="citation")
app.add_typer(src_app, name="src")
app.add_typer(doc_app, name="doc")
app.add_typer(graph_app, name="graph")


def main() -> None:
    app()
