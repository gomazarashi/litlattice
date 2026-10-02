"""Tests for the OpenAlex provider adapter. No network access."""

import http.client
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Self

import pytest

from litlattice.errors import ProviderError
from litlattice.openalex import (
    API_KEY_ENV_VAR,
    DEFAULT_BASE_URL,
    OpenAlexClient,
    _to_work,
)
from litlattice.providers import ProviderWork

SELECT = "id,doi,display_name,publication_year,locations"
SELECT_WITH_REFERENCES = f"{SELECT},referenced_works"


def _url_key(url: str) -> tuple[str, frozenset[tuple[str, str]]]:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    return parts.path, frozenset(query)


def _route(path: str, **params: str) -> tuple[str, frozenset[tuple[str, str]]]:
    return (path, frozenset(params.items()))


class FakeFetch:
    def __init__(self, routes: dict[Any, dict[str, Any] | None]) -> None:
        self._routes = routes
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any] | None:
        self.urls.append(url)
        key = _url_key(url)
        if key not in self._routes:
            raise AssertionError(f"unexpected request: {url}")
        return self._routes[key]

    def paths(self) -> list[str]:
        return [urllib.parse.urlsplit(url).path for url in self.urls]

    def query(self, index: int) -> dict[str, str]:
        query = urllib.parse.urlsplit(self.urls[index]).query
        return dict(urllib.parse.parse_qsl(query))


def _work_payload(
    work_id: str,
    *,
    doi: str | None = None,
    title: str = "A work",
    year: int = 2024,
    references: list[str] | None = None,
    locations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": f"https://openalex.org/{work_id}",
        "doi": doi,
        "display_name": title,
        "publication_year": year,
    }
    if references is not None:
        payload["referenced_works"] = [
            f"https://openalex.org/{rid}" for rid in references
        ]
    if locations is not None:
        payload["locations"] = locations
    return payload


def _arxiv_route(
    arxiv_id: str, scheme: str = "http"
) -> tuple[str, frozenset[tuple[str, str]]]:
    return _route(
        "/works",
        filter=f"locations.landing_page_url:{scheme}://arxiv.org/abs/{arxiv_id}",
        per_page="2",
        select=SELECT,
    )


def _openalex_ids(works: list[ProviderWork]) -> list[str]:
    return [
        value
        for work in works
        for scheme, value in work.identifiers
        if scheme == "openalex"
    ]


def test_default_base_url() -> None:
    assert DEFAULT_BASE_URL == "https://api.openalex.org"


def test_lookup_work_prefers_openalex_over_doi() -> None:
    fake = FakeFetch({_route("/works/W2", select=SELECT): _work_payload("W2")})
    client = OpenAlexClient(fetch_json=fake)

    work = client.lookup_work([("doi", "10.1000/abc"), ("openalex", "W2")])

    assert work == ProviderWork(
        identifiers=(("openalex", "W2"),),
        title="A work",
        publication_year=2024,
    )
    assert fake.paths() == ["/works/W2"]


def test_lookup_work_uses_normalized_doi() -> None:
    fake = FakeFetch(
        {
            _route("/works/doi:10.1000/abc", select=SELECT): _work_payload(
                "W3", doi="https://doi.org/10.1000/ABC"
            )
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    work = client.lookup_work([("doi", "https://doi.org/10.1000/ABC")])

    assert work is not None
    assert work.identifiers == (("openalex", "W3"), ("doi", "10.1000/ABC"))
    assert fake.paths() == ["/works/doi:10.1000/abc"]


def test_lookup_work_uses_arxiv_landing_page_filter() -> None:
    fake = FakeFetch({_arxiv_route("2401.12345"): {"results": [_work_payload("W4")]}})
    client = OpenAlexClient(fetch_json=fake)

    work = client.lookup_work([("arxiv", "arXiv:2401.12345v2")])

    assert work is not None
    assert work.identifiers == (("openalex", "W4"),)
    assert fake.paths() == ["/works"]
    assert fake.query(0)["per_page"] == "2"
    assert fake.query(0)["filter"] == (
        "locations.landing_page_url:http://arxiv.org/abs/2401.12345"
    )
    assert fake.query(0)["select"] == SELECT


def test_lookup_work_returns_none_when_arxiv_lookup_is_empty() -> None:
    fake = FakeFetch(
        {
            _arxiv_route("2401.12345"): {"results": []},
            _arxiv_route("2401.12345", "https"): {"results": []},
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    assert client.lookup_work([("arxiv", "2401.12345")]) is None


def test_arxiv_lookup_falls_back_to_https_landing_page() -> None:
    fake = FakeFetch(
        {
            _arxiv_route("2401.12345"): {"results": []},
            _arxiv_route("2401.12345", "https"): {"results": [_work_payload("W7")]},
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    work = client.lookup_work([("arxiv", "2401.12345")])

    assert work is not None
    assert ("openalex", "W7") in work.identifiers


def test_lookup_work_returns_none_when_arxiv_lookup_is_ambiguous() -> None:
    fake = FakeFetch(
        {
            _arxiv_route("2401.12345"): {
                "results": [_work_payload("W1"), _work_payload("W2")]
            }
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    assert client.lookup_work([("arxiv", "2401.12345")]) is None


def test_lookup_work_falls_through_missing_identifiers() -> None:
    fake = FakeFetch(
        {
            _route("/works/W1", select=SELECT): None,
            _route("/works/doi:10.1000/abc", select=SELECT): _work_payload("W2"),
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    work = client.lookup_work([("openalex", "W1"), ("doi", "10.1000/abc")])

    assert work is not None
    assert work.identifiers == (("openalex", "W2"),)
    assert fake.paths() == ["/works/W1", "/works/doi:10.1000/abc"]


def test_lookup_work_tries_arxiv_after_doi_miss() -> None:
    fake = FakeFetch(
        {
            _route("/works/doi:10.1000/abc", select=SELECT): None,
            _arxiv_route("2401.12345"): {"results": [_work_payload("W5")]},
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    work = client.lookup_work([("doi", "10.1000/abc"), ("arxiv", "2401.12345")])

    assert work is not None
    assert work.identifiers == (("openalex", "W5"),)
    assert fake.paths() == ["/works/doi:10.1000/abc", "/works"]


def test_lookup_work_without_usable_identifiers_makes_no_request() -> None:
    fake = FakeFetch({})
    client = OpenAlexClient(fetch_json=fake)

    assert client.lookup_work([("pubmed", "12345"), ("doi", "not-a-doi")]) is None
    assert fake.urls == []


def test_to_work_converts_full_record() -> None:
    work = _to_work(
        {
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.48550/arXiv.2401.12345",
            "display_name": "Attention",
            "publication_year": 2017,
        }
    )

    assert work == ProviderWork(
        identifiers=(
            ("openalex", "W123"),
            ("doi", "10.48550/arXiv.2401.12345"),
            ("arxiv", "2401.12345"),
        ),
        title="Attention",
        publication_year=2017,
    )


def test_to_work_strips_doi_url_prefix_and_keeps_case() -> None:
    work = _to_work(
        {"id": "https://openalex.org/W1", "doi": "https://dx.doi.org/10.1000/ABC"}
    )

    assert work is not None
    assert work.identifiers == (("openalex", "W1"), ("doi", "10.1000/ABC"))


@pytest.mark.parametrize(
    "doi",
    [
        "https://doi.org/10.48550/ARXIV.1706.03762",
        "https://doi.org/10.48550/arXiv.1706.03762",
    ],
)
def test_to_work_adds_arxiv_identifier_case_insensitively(doi: str) -> None:
    work = _to_work({"id": "https://openalex.org/W1", "doi": doi})

    assert work is not None
    assert ("arxiv", "1706.03762") in work.identifiers


def test_to_work_handles_old_style_arxiv_doi() -> None:
    work = _to_work(
        {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.48550/arXiv.hep-th/9901001",
        }
    )

    assert work is not None
    assert ("arxiv", "hep-th/9901001") in work.identifiers


def test_to_work_adds_arxiv_from_location_when_doi_is_null() -> None:
    work = _to_work(
        {
            "id": "https://openalex.org/W1",
            "doi": None,
            "locations": [
                {
                    "landing_page_url": "https://arxiv.org/abs/2401.12345v2",
                    "is_oa": True,
                }
            ],
        }
    )

    assert work is not None
    assert ("arxiv", "2401.12345") in work.identifiers


def test_to_work_adds_arxiv_from_datacite_location_url_when_doi_is_null() -> None:
    work = _to_work(
        {
            "id": "https://openalex.org/W1",
            "doi": None,
            "locations": [
                {"landing_page_url": "https://doi.org/10.48550/arXiv.2401.12345"},
            ],
        }
    )

    assert work is not None
    assert ("arxiv", "2401.12345") in work.identifiers


def test_to_work_ignores_arxiv_locations_when_doi_is_not_arxiv() -> None:
    work = _to_work(
        {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.1000/ABC",
            "locations": [
                {"landing_page_url": "https://arxiv.org/abs/2401.12345"},
            ],
        }
    )

    assert work is not None
    assert work.identifiers == (("openalex", "W1"), ("doi", "10.1000/ABC"))


def test_to_work_ignores_multiple_distinct_arxiv_locations() -> None:
    work = _to_work(
        {
            "id": "https://openalex.org/W1",
            "doi": None,
            "locations": [
                {"landing_page_url": "https://arxiv.org/abs/2401.12345"},
                {"landing_page_url": "http://arxiv.org/abs/2402.54321"},
            ],
        }
    )

    assert work == ProviderWork(identifiers=(("openalex", "W1"),))


def test_to_work_deduplicates_arxiv_locations_for_one_id() -> None:
    work = _to_work(
        {
            "id": "https://openalex.org/W1",
            "doi": None,
            "locations": [
                {"landing_page_url": "http://arxiv.org/abs/2401.12345"},
                {"landing_page_url": "https://arxiv.org/abs/2401.12345v3"},
            ],
        }
    )

    assert work is not None
    assert work.identifiers == (("openalex", "W1"), ("arxiv", "2401.12345"))


def test_to_work_drops_invalid_doi() -> None:
    work = _to_work({"id": "https://openalex.org/W1", "doi": "not-a-doi"})

    assert work is not None
    assert work.identifiers == (("openalex", "W1"),)


def test_to_work_without_usable_id_is_dropped() -> None:
    assert _to_work({"doi": "https://doi.org/10.1000/abc"}) is None
    assert _to_work({"id": "https://openalex.org/A123"}) is None
    assert _to_work({"id": None}) is None


def test_to_work_empty_display_name_and_bad_year_become_none() -> None:
    work = _to_work(
        {
            "id": "https://openalex.org/W1",
            "display_name": "",
            "publication_year": "2020",
        }
    )

    assert work is not None
    assert work.title is None
    assert work.publication_year is None


def test_to_work_missing_doi_and_title() -> None:
    work = _to_work({"id": "https://openalex.org/W1", "doi": None})

    assert work == ProviderWork(identifiers=(("openalex", "W1"),))


def test_fetch_references_preserves_order_and_fills_missing() -> None:
    fake = FakeFetch(
        {
            _route("/works/W9", select=SELECT_WITH_REFERENCES): _work_payload(
                "W9", references=["W3", "W1", "W2"]
            ),
            _route(
                "/works",
                filter="openalex_id:W3|W1|W2",
                per_page="50",
                select=SELECT,
            ): {
                "results": [
                    _work_payload("W2", title="Two"),
                    _work_payload("W3", title="Three"),
                ]
            },
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.fetch_references(
        ProviderWork(identifiers=(("openalex", "W9"),)), limit=10
    )

    assert _openalex_ids(works) == ["W3", "W1", "W2"]
    assert works[0].title == "Three"
    assert works[1] == ProviderWork(identifiers=(("openalex", "W1"),))
    assert works[2].title == "Two"
    assert fake.paths() == ["/works/W9", "/works"]


def test_fetch_references_respects_limit() -> None:
    fake = FakeFetch(
        {
            _route("/works/W9", select=SELECT_WITH_REFERENCES): _work_payload(
                "W9", references=["W1", "W2", "W3"]
            ),
            _route(
                "/works", filter="openalex_id:W1|W2", per_page="50", select=SELECT
            ): {"results": []},
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.fetch_references(
        ProviderWork(identifiers=(("openalex", "W9"),)), limit=2
    )

    assert _openalex_ids(works) == ["W1", "W2"]
    assert fake.query(1)["filter"] == "openalex_id:W1|W2"


def test_fetch_references_splits_into_batches_of_fifty() -> None:
    ids = [f"W{i}" for i in range(1, 121)]
    fake = FakeFetch(
        {
            _route("/works/W999", select=SELECT_WITH_REFERENCES): _work_payload(
                "W999", references=ids
            ),
            _route(
                "/works",
                filter="openalex_id:" + "|".join(ids[0:50]),
                per_page="50",
                select=SELECT,
            ): {"results": []},
            _route(
                "/works",
                filter="openalex_id:" + "|".join(ids[50:100]),
                per_page="50",
                select=SELECT,
            ): {"results": []},
            _route(
                "/works",
                filter="openalex_id:" + "|".join(ids[100:120]),
                per_page="50",
                select=SELECT,
            ): {"results": []},
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.fetch_references(
        ProviderWork(identifiers=(("openalex", "W999"),)), limit=500
    )

    assert _openalex_ids(works) == ids
    assert len(fake.urls) == 4
    assert fake.query(1)["filter"] == "openalex_id:" + "|".join(ids[0:50])
    assert fake.query(2)["filter"] == "openalex_id:" + "|".join(ids[50:100])
    assert fake.query(3)["filter"] == "openalex_id:" + "|".join(ids[100:120])
    assert all(fake.query(i)["per_page"] == "50" for i in (1, 2, 3))


def test_fetch_references_with_zero_limit_makes_no_request() -> None:
    fake = FakeFetch({})
    client = OpenAlexClient(fetch_json=fake)

    assert (
        client.fetch_references(
            ProviderWork(identifiers=(("openalex", "W9"),)), limit=0
        )
        == []
    )
    assert fake.urls == []


def test_fetch_references_returns_empty_when_single_work_is_missing() -> None:
    fake = FakeFetch({_route("/works/W9", select=SELECT_WITH_REFERENCES): None})
    client = OpenAlexClient(fetch_json=fake)

    assert (
        client.fetch_references(
            ProviderWork(identifiers=(("openalex", "W9"),)), limit=5
        )
        == []
    )


def test_fetch_references_resolves_openalex_id_from_doi() -> None:
    fake = FakeFetch(
        {
            _route("/works/doi:10.1000/abc", select=SELECT): _work_payload(
                "W9", references=["W1"]
            ),
            _route("/works/W9", select=SELECT_WITH_REFERENCES): _work_payload(
                "W9", references=["W1"]
            ),
            _route("/works", filter="openalex_id:W1", per_page="50", select=SELECT): {
                "results": [_work_payload("W1")]
            },
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.fetch_references(
        ProviderWork(identifiers=(("doi", "10.1000/ABC"),)), limit=5
    )

    assert _openalex_ids(works) == ["W1"]
    assert fake.paths() == ["/works/doi:10.1000/abc", "/works/W9", "/works"]


def test_fetch_references_returns_empty_without_openalex_id() -> None:
    fake = FakeFetch({})
    client = OpenAlexClient(fetch_json=fake)

    assert (
        client.fetch_references(
            ProviderWork(identifiers=(("pubmed", "12345"),)), limit=5
        )
        == []
    )
    assert fake.urls == []


def test_fetch_citations_follows_cursor_pages() -> None:
    fake = FakeFetch(
        {
            _route(
                "/works",
                filter="cites:W9",
                sort="cited_by_count:desc",
                per_page="10",
                cursor="*",
                select=SELECT,
            ): {
                "results": [_work_payload("W1"), _work_payload("W2")],
                "meta": {"next_cursor": "next-page"},
            },
            _route(
                "/works",
                filter="cites:W9",
                sort="cited_by_count:desc",
                per_page="10",
                cursor="next-page",
                select=SELECT,
            ): {"results": [_work_payload("W3")], "meta": {"next_cursor": None}},
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.fetch_citations(
        ProviderWork(identifiers=(("openalex", "W9"),)), limit=10
    )

    assert _openalex_ids(works) == ["W1", "W2", "W3"]
    assert len(fake.urls) == 2
    assert fake.query(1)["cursor"] == "next-page"


def test_fetch_citations_stops_at_limit() -> None:
    fake = FakeFetch(
        {
            _route(
                "/works",
                filter="cites:W9",
                sort="cited_by_count:desc",
                per_page="2",
                cursor="*",
                select=SELECT,
            ): {
                "results": [
                    _work_payload("W1"),
                    _work_payload("W2"),
                    _work_payload("W3"),
                ],
                "meta": {"next_cursor": "next-page"},
            },
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.fetch_citations(
        ProviderWork(identifiers=(("openalex", "W9"),)), limit=2
    )

    assert _openalex_ids(works) == ["W1", "W2"]
    assert len(fake.urls) == 1


def test_fetch_citations_stops_on_empty_results() -> None:
    fake = FakeFetch(
        {
            _route(
                "/works",
                filter="cites:W9",
                sort="cited_by_count:desc",
                per_page="10",
                cursor="*",
                select=SELECT,
            ): {"results": [], "meta": {"next_cursor": "next-page"}},
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    assert (
        client.fetch_citations(
            ProviderWork(identifiers=(("openalex", "W9"),)), limit=10
        )
        == []
    )
    assert len(fake.urls) == 1


def test_fetch_citations_caps_per_page_at_two_hundred() -> None:
    fake = FakeFetch(
        {
            _route(
                "/works",
                filter="cites:W9",
                sort="cited_by_count:desc",
                per_page="200",
                cursor="*",
                select=SELECT,
            ): {"results": [_work_payload("W1")], "meta": {"next_cursor": None}},
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.fetch_citations(
        ProviderWork(identifiers=(("openalex", "W9"),)), limit=500
    )

    assert _openalex_ids(works) == ["W1"]
    assert fake.query(0)["per_page"] == "200"


def test_fetch_citations_deduplicates_works() -> None:
    fake = FakeFetch(
        {
            _route(
                "/works",
                filter="cites:W9",
                sort="cited_by_count:desc",
                per_page="10",
                cursor="*",
                select=SELECT,
            ): {
                "results": [_work_payload("W1"), _work_payload("W1")],
                "meta": {"next_cursor": None},
            },
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.fetch_citations(
        ProviderWork(identifiers=(("openalex", "W9"),)), limit=10
    )

    assert _openalex_ids(works) == ["W1"]


def test_fetch_citations_with_zero_limit_makes_no_request() -> None:
    fake = FakeFetch({})
    client = OpenAlexClient(fetch_json=fake)

    assert (
        client.fetch_citations(ProviderWork(identifiers=(("openalex", "W9"),)), limit=0)
        == []
    )
    assert fake.urls == []


def test_search_works_sends_query_per_page_and_select() -> None:
    fake = FakeFetch(
        {
            _route("/works", search="attention", per_page="5", select=SELECT): {
                "results": [_work_payload("W1", title="Attention")]
            }
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.search_works("attention", limit=5)

    assert _openalex_ids(works) == ["W1"]
    assert works[0].title == "Attention"
    assert fake.paths() == ["/works"]
    assert fake.query(0)["search"] == "attention"
    assert fake.query(0)["per_page"] == "5"
    assert fake.query(0)["select"] == SELECT


def test_search_works_sends_api_key() -> None:
    fake = FakeFetch(
        {
            _route(
                "/works", search="q", per_page="10", select=SELECT, api_key="secret"
            ): {"results": []}
        }
    )
    client = OpenAlexClient(api_key="secret", fetch_json=fake)

    assert client.search_works("q", limit=10) == []
    assert fake.query(0)["api_key"] == "secret"


def test_search_works_caps_per_page_at_two_hundred() -> None:
    fake = FakeFetch(
        {_route("/works", search="q", per_page="200", select=SELECT): {"results": []}}
    )
    client = OpenAlexClient(fetch_json=fake)

    assert client.search_works("q", limit=500) == []
    assert fake.query(0)["per_page"] == "200"


def test_search_works_respects_limit() -> None:
    fake = FakeFetch(
        {
            _route("/works", search="q", per_page="2", select=SELECT): {
                "results": [
                    _work_payload("W1"),
                    _work_payload("W2"),
                    _work_payload("W3"),
                ]
            }
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.search_works("q", limit=2)

    assert _openalex_ids(works) == ["W1", "W2"]


def test_search_works_deduplicates_and_skips_unusable_results() -> None:
    fake = FakeFetch(
        {
            _route("/works", search="q", per_page="10", select=SELECT): {
                "results": [
                    _work_payload("W1"),
                    {"id": "https://openalex.org/A1"},
                    "junk",
                    _work_payload("W1"),
                    _work_payload("W2"),
                ]
            }
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    works = client.search_works("q", limit=10)

    assert _openalex_ids(works) == ["W1", "W2"]


def test_search_works_returns_empty_on_404() -> None:
    fake = FakeFetch({_route("/works", search="q", per_page="10", select=SELECT): None})
    client = OpenAlexClient(fetch_json=fake)

    assert client.search_works("q", limit=10) == []


def test_search_works_returns_empty_on_malformed_results() -> None:
    fake = FakeFetch(
        {
            _route("/works", search="q", per_page="10", select=SELECT): {
                "results": "nope"
            }
        }
    )
    client = OpenAlexClient(fetch_json=fake)

    assert client.search_works("q", limit=10) == []


def test_search_works_without_query_or_limit_makes_no_request() -> None:
    fake = FakeFetch({})
    client = OpenAlexClient(fetch_json=fake)

    assert client.search_works("   ", limit=5) == []
    assert client.search_works("q", limit=0) == []
    assert fake.urls == []


def test_api_key_is_sent_when_configured() -> None:
    fake = FakeFetch(
        {_route("/works/W1", select=SELECT, api_key="secret"): _work_payload("W1")}
    )
    client = OpenAlexClient(api_key="secret", fetch_json=fake)

    assert client.lookup_work([("openalex", "W1")]) is not None
    assert fake.query(0)["api_key"] == "secret"


def test_api_key_is_omitted_without_configuration() -> None:
    fake = FakeFetch({_route("/works/W1", select=SELECT): _work_payload("W1")})
    client = OpenAlexClient(fetch_json=fake)

    assert client.lookup_work([("openalex", "W1")]) is not None
    assert "api_key" not in fake.query(0)


def test_from_environment_reads_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, "env-key")

    assert OpenAlexClient.from_environment()._api_key == "env-key"


def test_from_environment_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)

    assert OpenAlexClient.from_environment()._api_key is None


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class FakeErrorResponse(FakeResponse):
    def __init__(self, error: Exception) -> None:
        super().__init__(b"")
        self._error = error

    def read(self) -> bytes:
        raise self._error


def _http_error(
    url: str, code: int, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", headers, None)


def _install_urlopen(
    monkeypatch: pytest.MonkeyPatch, responses: list[Any]
) -> list[urllib.request.Request]:
    calls: list[urllib.request.Request] = []

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> Any:
        calls.append(request)
        if not responses:
            raise AssertionError("unexpected urlopen call")
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_http_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.openalex.org/works/W1?select=id"
    calls = _install_urlopen(monkeypatch, [_http_error(url, 404)])
    sleeps: list[float] = []
    client = OpenAlexClient(sleep=sleeps.append)

    assert client.lookup_work([("openalex", "W1")]) is None
    assert len(calls) == 1
    assert sleeps == []


def test_http_retries_429_after_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.openalex.org/works/W1?select=id"
    calls = _install_urlopen(
        monkeypatch,
        [
            _http_error(url, 429, {"Retry-After": "2"}),
            FakeResponse(b'{"id": "https://openalex.org/W1"}'),
        ],
    )
    sleeps: list[float] = []
    client = OpenAlexClient(sleep=sleeps.append)

    assert client.lookup_work([("openalex", "W1")]) is not None
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_http_caps_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.openalex.org/works/W1?select=id"
    _install_urlopen(
        monkeypatch,
        [
            _http_error(url, 429, {"Retry-After": "120"}),
            FakeResponse(b'{"id": "https://openalex.org/W1"}'),
        ],
    )
    sleeps: list[float] = []
    client = OpenAlexClient(sleep=sleeps.append)

    assert client.lookup_work([("openalex", "W1")]) is not None
    assert sleeps == [30.0]


def test_http_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.openalex.org/works/W1?select=id"
    calls = _install_urlopen(monkeypatch, [_http_error(url, 503) for _ in range(4)])
    sleeps: list[float] = []
    client = OpenAlexClient(sleep=sleeps.append)

    with pytest.raises(ProviderError):
        client.lookup_work([("openalex", "W1")])

    assert len(calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_http_url_error_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_urlopen(monkeypatch, [urllib.error.URLError("boom")])
    client = OpenAlexClient(sleep=lambda _: None)

    with pytest.raises(ProviderError):
        client.lookup_work([("openalex", "W1")])


def test_http_timeout_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_urlopen(monkeypatch, [TimeoutError("timed out")])
    client = OpenAlexClient(sleep=lambda _: None)

    with pytest.raises(ProviderError):
        client.lookup_work([("openalex", "W1")])


def test_http_incomplete_read_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_urlopen(monkeypatch, [FakeErrorResponse(http.client.IncompleteRead(b""))])
    client = OpenAlexClient(sleep=lambda _: None)

    with pytest.raises(ProviderError):
        client.lookup_work([("openalex", "W1")])


def test_http_connection_reset_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_urlopen(monkeypatch, [ConnectionResetError("reset")])
    client = OpenAlexClient(sleep=lambda _: None)

    with pytest.raises(ProviderError):
        client.lookup_work([("openalex", "W1")])


@pytest.mark.parametrize(
    "failure",
    [http.client.IncompleteRead(b""), ConnectionResetError("reset")],
)
def test_transport_error_does_not_leak_api_key(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> Any:
        requests.append(request)
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAlexClient(api_key="secret-key", sleep=lambda _: None)

    with pytest.raises(ProviderError) as excinfo:
        client.lookup_work([("openalex", "W1")])

    assert "api_key=secret-key" in requests[0].full_url
    assert "secret-key" not in str(excinfo.value)
    assert "/works/W1" in str(excinfo.value)


def test_http_invalid_json_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_urlopen(monkeypatch, [FakeResponse(b"not json")])
    client = OpenAlexClient(sleep=lambda _: None)

    with pytest.raises(ProviderError):
        client.lookup_work([("openalex", "W1")])


def test_http_sets_user_agent_and_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_urlopen(monkeypatch, [FakeResponse(b"{}")])
    client = OpenAlexClient(sleep=lambda _: None)

    client.lookup_work([("openalex", "W1")])

    request = calls[0]
    assert request.get_header("User-agent").startswith("litlattice/")
    assert "(+https://github.com/gomazarashi/litlattice)" in request.get_header(
        "User-agent"
    )
    assert request.get_header("Accept") == "application/json"


def test_provider_error_does_not_leak_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> Any:
        requests.append(request)
        raise _http_error(request.full_url, 500)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAlexClient(api_key="sekrit", sleep=lambda _: None)

    with pytest.raises(ProviderError) as excinfo:
        client.lookup_work([("openalex", "W1")])

    assert "api_key=sekrit" in requests[0].full_url
    message = str(excinfo.value)
    assert "sekrit" not in message
    assert "/works/W1" in message
