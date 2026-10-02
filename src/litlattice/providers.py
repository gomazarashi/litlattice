"""The OpenAlex operations used by Core and their work result."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderWork:
    """A work as described by a provider.

    ``identifiers`` are ``(scheme, value)`` pairs that the provider asserts
    denote this one work, including the provider's own ID.
    """

    identifiers: tuple[tuple[str, str], ...]
    title: str | None = None
    publication_year: int | None = None


class PaperProvider(Protocol):
    def lookup_work(
        self, identifiers: Sequence[tuple[str, str]]
    ) -> ProviderWork | None:
        """The work denoted by any of the identifiers, or ``None`` if unknown."""
        ...

    def search_works(self, query: str, *, limit: int) -> list[ProviderWork]:
        """Works matching a free-text query (e.g. a title), best match first, at most ``limit``."""
        ...

    def fetch_references(self, work: ProviderWork, *, limit: int) -> list[ProviderWork]:
        """Works that ``work`` cites, at most ``limit``."""
        ...

    def fetch_citations(self, work: ProviderWork, *, limit: int) -> list[ProviderWork]:
        """Works that cite ``work``, at most ``limit``."""
        ...
