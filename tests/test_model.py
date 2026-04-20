from unittest.mock import MagicMock

import pytest

from pricing.model import (
    ClothingPriceModel,
    DEFAULT_DOMAINS,
    RemoteLookupError,
    _default_http_get_raw,
    _extract_ddg_urls,
    _extract_price_from_offers,
    _extract_product_from_page,
    _parse_jsonld_product,
)


def test_batch_search_returns_online_matches():
    http_get = MagicMock(
        side_effect=[
            {
                "products": [
                    {"brand": "Nike", "title": "Air Zoom Pegasus 40", "price": 130},
                    {"brand": "Adidas", "title": "Ultraboost Light", "price": 190},
                ]
            },
            {
                "products": [
                    {"brand": "Adidas", "title": "Ultraboost Light", "price": 190},
                ]
            },
        ]
    )

    model = ClothingPriceModel(http_get=http_get, web_search=False, limit=5)

    results = model.batch_search([("Nike", "Pegasus 40"), ("Adidas", "Ultraboost")], min_score=0.3)

    assert http_get.call_count == 2
    assert results[0] is not None
    assert results[0].brand == "Nike"
    assert results[0].price == 130
    assert results[1] is not None and results[1].brand == "Adidas"


def test_batch_search_handles_remote_errors():
    http_get = MagicMock(side_effect=RemoteLookupError("boom"))

    model = ClothingPriceModel(http_get=http_get, web_search=False)

    results = model.batch_search([("Brand", "Item")])

    assert results == [None]
    assert http_get.called


@pytest.mark.parametrize(
    "payload,expected_count",
    [
        (
            {
                "products": [
                    {"brand": "Zara", "title": "Textured Blazer", "price": "89.99"},
                    {"brand": "Zara", "title": "", "price": 39},
                    {"brand": "", "title": "Wide Leg Jeans", "price": 59},
                    {"brand": "Zara", "title": "Wide Leg Jeans", "price": "not a number"},
                ]
            },
            1,
        ),
        ({"products": []}, 0),
    ],
)
def test_fetch_products_filters_invalid_entries(payload, expected_count):
    http_get = MagicMock(return_value=payload)

    model = ClothingPriceModel(http_get=http_get, web_search=False)

    products = model._fetch_products("Zara", "Jeans")

    assert len(products) == expected_count
    if products:
        assert products[0].title == "Textured Blazer"

    http_get.assert_called_with(
        model.base_url,
        {"q": "Zara Jeans", "limit": str(model.limit)},
        model.timeout,
    )


# ---------------------------------------------------------------------------
# Domain-based search tests
# ---------------------------------------------------------------------------

_DDG_HTML_TEMPLATE = """\
<html><body>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg={encoded_url}&rut=abc">Product page</a>
</div>
</body></html>
"""

_PRODUCT_PAGE_JSONLD = """\
<html><head>
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "{title}",
  "brand": {{"@type": "Brand", "name": "{brand}"}},
  "offers": {{
    "@type": "Offer",
    "price": "{price}",
    "priceCurrency": "USD"
  }}
}}
</script>
</head><body></body></html>
"""


def _build_ddg_html(url: str) -> str:
    from urllib.parse import quote
    return _DDG_HTML_TEMPLATE.format(encoded_url=quote(url, safe=""))


def _build_ddg_html_for_urls(urls):
    from urllib.parse import quote
    return "".join(f'<a href="//duckduckgo.com/l/?uddg={quote(url, safe="")}">x</a>' for url in urls)


def _build_product_page(brand: str, title: str, price: str) -> str:
    return _PRODUCT_PAGE_JSONLD.format(brand=brand, title=title, price=price)


def test_default_domains_not_empty():
    assert isinstance(DEFAULT_DOMAINS, list)
    assert len(DEFAULT_DOMAINS) > 0
    for domain in DEFAULT_DOMAINS:
        assert "." in domain


def test_domain_mode_queries_ddg_and_fetches_pages():
    product_url = "https://www.example-store.com/product/pegasus"
    ddg_html = _build_ddg_html(product_url)
    product_html = _build_product_page("Nike", "Air Zoom Pegasus 40", "130.00")

    http_get_raw = MagicMock(side_effect=[ddg_html, product_html])

    model = ClothingPriceModel(
        domains=["www.example-store.com"],
        limit=5,
        http_get_raw=http_get_raw,
    )

    results = model.batch_search([("Nike", "Pegasus 40")], min_score=0.3)

    assert http_get_raw.call_count == 2
    assert results[0] is not None
    assert results[0].brand == "Nike"
    assert results[0].price == 130.0


def test_domain_mode_skips_failing_domains():
    """Errors from individual domains must not crash the run."""
    http_get_raw = MagicMock(side_effect=RemoteLookupError("timeout"))

    model = ClothingPriceModel(
        domains=["www.broken-store.com"],
        limit=5,
        http_get_raw=http_get_raw,
    )

    results = model.batch_search([("Nike", "Pegasus 40")])

    assert results == [None]


def test_domain_mode_multiple_domains_stops_at_limit():
    """Results are capped at --limit across all domains."""
    product_url = "https://www.store-a.com/p/1"
    ddg_html = _build_ddg_html(product_url)
    product_html = _build_product_page("Nike", "Pegasus 40", "129.99")

    http_get_raw = MagicMock(return_value=ddg_html)
    # Second call per domain fetches the product page
    http_get_raw.side_effect = [ddg_html, product_html, ddg_html, product_html]

    model = ClothingPriceModel(
        domains=["www.store-a.com", "www.store-b.com"],
        limit=1,
        http_get_raw=http_get_raw,
    )
    products = model._fetch_products_from_domains("Nike", "Pegasus 40")
    # limit=1 so should return at most 1 product
    assert len(products) <= 1


def test_domain_mode_empty_ddg_response_skips_page_fetch():
    """When DDG returns no result URLs, no product pages should be fetched."""
    http_get_raw = MagicMock(return_value="<html><body>No results</body></html>")

    model = ClothingPriceModel(
        domains=["www.example-store.com"],
        limit=5,
        http_get_raw=http_get_raw,
    )
    products = model._fetch_products("Nike", "Pegasus 40")
    # Only the DDG search request was made; no product page fetches
    assert http_get_raw.call_count == 1
    assert products == []


def test_extract_ddg_urls_parses_uddg_links():
    from urllib.parse import quote
    url = "https://www.nordstrom.com/product/pegasus"
    html = f'<a href="//duckduckgo.com/l/?uddg={quote(url, safe="")}&rut=x">link</a>'
    urls = _extract_ddg_urls(html)
    assert urls == [url]


def test_extract_ddg_urls_fallback_direct_href():
    html = '<a class="result__a" href="https://www.zappos.com/product/shoes">shoes</a>'
    urls = _extract_ddg_urls(html)
    assert urls == ["https://www.zappos.com/product/shoes"]


def test_extract_ddg_urls_ignores_non_http():
    html = '<a href="//duckduckgo.com/l/?uddg=ftp%3A%2F%2Fold.server%2Ffile">x</a>'
    urls = _extract_ddg_urls(html)
    assert urls == []


def test_parse_jsonld_product_extracts_fields():
    data = {
        "@type": "Product",
        "name": "Wide Leg Jeans",
        "brand": {"@type": "Brand", "name": "Zara"},
        "offers": {"@type": "Offer", "price": "59.90"},
    }
    product = _parse_jsonld_product(data, "www.zara.com")
    assert product is not None
    assert product.brand == "Zara"
    assert product.title == "Wide Leg Jeans"
    assert product.price == pytest.approx(59.90)


def test_parse_jsonld_product_list_type():
    data = {
        "@type": ["Product", "Thing"],
        "name": "T-Shirt",
        "offers": {"price": "19.99"},
    }
    product = _parse_jsonld_product(data, "www.gap.com")
    assert product is not None
    assert product.title == "T-Shirt"
    assert product.price == pytest.approx(19.99)
    # brand defaults to domain when not present
    assert product.brand == "www.gap.com"


def test_parse_jsonld_product_aggregate_offer():
    data = {
        "@type": "Product",
        "name": "Sneakers",
        "offers": {"@type": "AggregateOffer", "lowPrice": "89.00", "highPrice": "120.00"},
    }
    product = _parse_jsonld_product(data, "www.store.com")
    assert product is not None
    assert product.price == pytest.approx(89.00)


def test_parse_jsonld_product_returns_none_without_price():
    data = {"@type": "Product", "name": "Jacket"}
    assert _parse_jsonld_product(data, "domain") is None


def test_parse_jsonld_product_returns_none_wrong_type():
    data = {"@type": "Organization", "name": "Nike Inc"}
    assert _parse_jsonld_product(data, "domain") is None


def test_extract_price_from_offers_list():
    offers = [
        {"@type": "Offer", "price": "45.00"},
        {"@type": "Offer", "price": "50.00"},
    ]
    assert _extract_price_from_offers(offers) == pytest.approx(45.00)


def test_extract_price_from_offers_none():
    assert _extract_price_from_offers(None) is None
    assert _extract_price_from_offers("not an offer") is None


def test_extract_product_from_page_jsonld():
    html = _build_product_page("Adidas", "Ultraboost Light", "190.00")
    product = _extract_product_from_page(html, "www.adidas.com", "Adidas")
    assert product is not None
    assert product.brand == "Adidas"
    assert product.title == "Ultraboost Light"
    assert product.price == pytest.approx(190.00)


def test_extract_product_from_page_meta_fallback():
    html = (
        '<html><head>'
        '<meta property="og:title" content="Pegasus 40"/>'
        '<meta property="product:price:amount" content="130.00"/>'
        '</head><body></body></html>'
    )
    product = _extract_product_from_page(html, "www.nike.com", "Nike")
    assert product is not None
    assert product.title == "Pegasus 40"
    assert product.price == pytest.approx(130.00)


def test_extract_product_from_page_returns_none_for_blank_page():
    assert _extract_product_from_page("<html><body>No data</body></html>", "domain", "brand") is None


def test_base_url_mode_unaffected_by_domain_param():
    """web_search=False should use the legacy base-URL JSON API."""
    http_get = MagicMock(return_value={"products": []})
    model = ClothingPriceModel(http_get=http_get, domains=None, web_search=False)
    model._fetch_products("Nike", "Shoes")
    http_get.assert_called_once_with(
        model.base_url,
        {"q": "Nike Shoes", "limit": str(model.limit)},
        model.timeout,
    )


# ---------------------------------------------------------------------------
# Open web search tests (new default)
# ---------------------------------------------------------------------------

def test_web_search_queries_ddg_without_site_restriction():
    """Default mode does an unrestricted DuckDuckGo search."""
    from urllib.parse import quote
    product_url = "https://www.some-store.com/product/pegasus-40"
    ddg_html = (
        f'<a href="//duckduckgo.com/l/?uddg={quote(product_url, safe="")}&rut=x">'
        'Pegasus 40</a>'
    )
    product_html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Air Zoom Pegasus 40",'
        '"brand":{"@type":"Brand","name":"Nike"},'
        '"offers":{"@type":"Offer","price":"130.00"}}'
        '</script>'
    )
    http_get_raw = MagicMock(side_effect=[ddg_html, product_html])

    # Default constructor: web_search=True, domains=None
    model = ClothingPriceModel(http_get_raw=http_get_raw, limit=5)

    results = model.batch_search([("Nike", "Pegasus 40")], min_score=0.3)

    # First call must be DuckDuckGo with no site: filter
    ddg_call_url = http_get_raw.call_args_list[0][0][0]
    assert "duckduckgo" in ddg_call_url
    assert "site:" not in ddg_call_url

    assert results[0] is not None
    assert results[0].brand == "Nike"
    assert results[0].price == pytest.approx(130.0)


def test_web_search_handles_ddg_failure_gracefully():
    """A DDG request error should not crash the run; return None."""
    http_get_raw = MagicMock(side_effect=RemoteLookupError("network error"))
    model = ClothingPriceModel(http_get_raw=http_get_raw)

    results = model.batch_search([("Nike", "Pegasus 40")])
    assert results == [None]


def test_web_search_skips_pages_with_no_price():
    """Pages that yield no product should be skipped; no crash."""
    from urllib.parse import quote
    url = "https://example.com/product/1"
    ddg_html = f'<a href="//duckduckgo.com/l/?uddg={quote(url, safe="")}">x</a>'
    product_html = "<html><body>No structured data here</body></html>"

    http_get_raw = MagicMock(side_effect=[ddg_html, product_html])
    model = ClothingPriceModel(http_get_raw=http_get_raw, limit=5)

    results = model.batch_search([("Nike", "Pegasus 40")])
    assert results == [None]


def test_default_http_get_raw_wraps_timeout_errors(monkeypatch):
    response = MagicMock()
    response.headers.get_content_charset.return_value = "utf-8"
    response.read.side_effect = TimeoutError("The read operation timed out")

    urlopen_mock = MagicMock()
    urlopen_mock.return_value.__enter__.return_value = response
    monkeypatch.setattr("pricing.model.urlopen", urlopen_mock)

    with pytest.raises(RemoteLookupError) as exc_info:
        _default_http_get_raw("https://example.com", 1.0)
    assert isinstance(exc_info.value.__cause__, TimeoutError)


def test_fetch_products_from_web_returns_empty_when_all_page_fetches_fail():
    urls = ["https://example.com/p/1", "https://example.com/p/2"]
    ddg_html = _build_ddg_html_for_urls(urls)
    http_get_raw = MagicMock(
        side_effect=[ddg_html, RemoteLookupError("timeout"), RemoteLookupError("timeout")]
    )
    model = ClothingPriceModel(http_get_raw=http_get_raw, limit=5)

    products = model._fetch_products_from_web("Nike", "Pegasus 40")
    assert products == []


def test_batch_search_returns_none_when_all_page_fetches_fail():
    urls = ["https://example.com/p/1", "https://example.com/p/2"]
    ddg_html = _build_ddg_html_for_urls(urls)
    http_get_raw = MagicMock(
        side_effect=[ddg_html, RemoteLookupError("timeout"), RemoteLookupError("timeout")]
    )
    model = ClothingPriceModel(http_get_raw=http_get_raw, limit=5)

    results = model.batch_search([("Nike", "Pegasus 40")])
    assert results == [None]


def test_web_search_respects_limit():
    """No more than limit pages should be fetched."""
    from urllib.parse import quote

    product_html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Pegasus 40","offers":{"price":"130"}}'
        '</script>'
    )

    # Build a DDG result page with 5 URLs
    urls = [f"https://store{i}.com/product" for i in range(5)]
    link_html = "".join(
        f'<a href="//duckduckgo.com/l/?uddg={quote(u, safe="")}">x</a>' for u in urls
    )
    http_get_raw = MagicMock(side_effect=[link_html] + [product_html] * 5)

    model = ClothingPriceModel(http_get_raw=http_get_raw, limit=2)
    products = model._fetch_products_from_web("Nike", "Pegasus 40")

    # Should stop after 2 successful extractions
    assert len(products) <= 2
