"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "paper",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("library_added_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper")),
    )
    op.create_table(
        "paper_identifier",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        sa.Column("scheme", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("normalized_value", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper.id"], name=op.f("fk_paper_identifier_paper_id_paper")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_identifier")),
        sa.UniqueConstraint(
            "scheme",
            "normalized_value",
            name=op.f("uq_paper_identifier_scheme_normalized_value"),
        ),
    )
    op.create_index(
        op.f("ix_paper_identifier_paper_id"),
        "paper_identifier",
        ["paper_id"],
        unique=False,
    )
    op.create_table(
        "source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("normalized_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_scanned_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source")),
        sa.UniqueConstraint("normalized_path", name=op.f("uq_source_normalized_path")),
    )
    op.create_table(
        "document_copy",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=True),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("normalized_path", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_mtime_ns", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("missing_since", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper.id"], name=op.f("fk_document_copy_paper_id_paper")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_copy")),
        sa.UniqueConstraint(
            "normalized_path", name=op.f("uq_document_copy_normalized_path")
        ),
    )
    op.create_index(
        op.f("ix_document_copy_content_hash"),
        "document_copy",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_copy_paper_id"), "document_copy", ["paper_id"], unique=False
    )
    op.create_table(
        "citation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("citing_paper_id", sa.Uuid(), nullable=False),
        sa.Column("cited_paper_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "citing_paper_id != cited_paper_id",
            name=op.f("ck_citation_no_self_citation"),
        ),
        sa.ForeignKeyConstraint(
            ["cited_paper_id"],
            ["paper.id"],
            name=op.f("fk_citation_cited_paper_id_paper"),
        ),
        sa.ForeignKeyConstraint(
            ["citing_paper_id"],
            ["paper.id"],
            name=op.f("fk_citation_citing_paper_id_paper"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_citation")),
        sa.UniqueConstraint(
            "citing_paper_id",
            "cited_paper_id",
            name=op.f("uq_citation_citing_paper_id_cited_paper_id"),
        ),
    )
    op.create_index(
        op.f("ix_citation_cited_paper_id"), "citation", ["cited_paper_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_citation_cited_paper_id"), table_name="citation")
    op.drop_table("citation")
    op.drop_index(op.f("ix_document_copy_paper_id"), table_name="document_copy")
    op.drop_index(op.f("ix_document_copy_content_hash"), table_name="document_copy")
    op.drop_table("document_copy")
    op.drop_table("source")
    op.drop_index(op.f("ix_paper_identifier_paper_id"), table_name="paper_identifier")
    op.drop_table("paper_identifier")
    op.drop_table("paper")
