"""SQLAlchemy ORM mapping of the LitLattice schema.

These classes describe persistence. They are not required to be the only
representation of the domain model.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint names keep SQLite batch migrations able to address them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store timezone-aware datetimes as UTC; load them back as aware UTC.

    Naive datetimes are rejected rather than guessed to be local time.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"Naive datetime is not allowed: {value!r}")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime,
        uuid.UUID: Uuid(as_uuid=True),
    }


class Paper(Base):
    """A paper that exists in the world. The title is metadata, not identity.

    ``library_added_at`` marks membership in the user's library; ``None``
    means the paper is known but not part of it.
    """

    __tablename__ = "paper"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(String)
    publication_year: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    library_added_at: Mapped[datetime | None]


class PaperIdentifier(Base):
    """An external identifier (DOI, arXiv ID, ...) attached to a Paper."""

    __tablename__ = "paper_identifier"
    __table_args__ = (UniqueConstraint("scheme", "normalized_value"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id"), index=True)
    scheme: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    normalized_value: Mapped[str] = mapped_column(String)


class Source(Base):
    """A file-system location that ``scan`` searches for documents.

    Identified by ``normalized_path``. DocumentCopies found under it do not
    reference it: removing a Source leaves them untouched.
    """

    __tablename__ = "source"
    __table_args__ = (UniqueConstraint("normalized_path"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    path: Mapped[str] = mapped_column(String)
    normalized_path: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    last_scanned_at: Mapped[datetime | None]


class DocumentCopy(Base):
    """A known copy of a document file; may exist without an identified Paper.

    Identified by ``normalized_path``. ``paper_id`` links the copy to the
    Paper it has been identified as, if any. The content hash is
    informational and deliberately not unique. ``file_size`` and
    ``file_mtime_ns`` record the file state the hash was computed from.
    """

    __tablename__ = "document_copy"
    __table_args__ = (UniqueConstraint("normalized_path"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper.id"), index=True
    )
    path: Mapped[str] = mapped_column(String)
    normalized_path: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str | None] = mapped_column(String, index=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_mtime_ns: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_at: Mapped[datetime | None]
    missing_since: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Citation(Base):
    """``citing_paper_id`` cites ``cited_paper_id`` (A → B)."""

    __tablename__ = "citation"
    __table_args__ = (
        UniqueConstraint("citing_paper_id", "cited_paper_id"),
        CheckConstraint("citing_paper_id != cited_paper_id", name="no_self_citation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    citing_paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id"))
    cited_paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
