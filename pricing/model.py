"""Utilities for discovering clothing prices from online product search APIs.

This module implements a lightweight open-web search based price lookup.
It intentionally does NOT support domain whitelists or a legacy base-URL JSON API.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import math
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

_DDGO_SEARCH_URL = "https://html.duckduckgo.com/html/"
_MAX_WEB_RESULTS = 10  # maximum result pages fetched during open web search
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

@dataclass(frozen=True)
class SearchResult:
    """Result returned for a clothing price search."""

    brand: str
    title: str
    price: float
    score: float

@dataclass(frozen=True)
class _Product:
    """Internal representation of a product returned by the remote lookup."""

    brand: str
    title: str
    price: float

    @property
    def normalized_brand(self) -> str:
        return _normalize_text(self.brand)

    @property
    def normalized_title(self) -> str:
        return _normalize_text(self.title)

class ClothingPriceModel:
    """Fetch clothing prices using open-web DuckDuckGo HTML search.

    Strategy:
      * For each query, run DuckDuckGo HTML search for: ``{brand} {title} price buy``.
      * Fetch a limited number of result pages.
      * Extract price + title from schema.org JSON-LD first, then Open Graph/meta tags.
      * Score candidates using token + sequence similarity and return the best match.

    Notes:
      * This model intentionally performs **open web search only**.
      * There are no default domains and no domain-filtering mode.
      * There is no legacy base_url JSON API mode.
    """

    def __init__(
        self,
        *,
        limit: int = 10,
        timeout: float = 10.0,
        http_get_raw: Optional[Callable[[str, float], str]] = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.limit = limit
        self.timeout = timeout
        self._http_get_raw = http_get_raw or _default_http_get_raw

    def close(self) -> None:
        """Provided for API compatibility; no persistent connections are kept."""
        return None

    def batch_search(
        self,
        queries: Iterable[Tuple[str, str]],
        *,
        min_score: float = 0.45,
    ) -> List[Optional[SearchResult]]:
        """Search for prices for a set of brand/title queries."""

        results: List[Optional[SearchResult]] = []
        for brand, title in queries:
            results.append(self._search_single(brand, title, min_score=min_score))
        return results

    def _search_single(
        self,
        brand: str,
        title: str,
        *,
        min_score: float,
    ) -> Optional[SearchResult]:
        if not brand or not title:
            return None

        normalized_brand = _normalize_text(brand)
        normalized_title = _normalize_text(title)

        try:
            products = self._fetch_products_from_web(brand, title)
        except RemoteLookupError:
            return None
        if not products:
            return None

        best_product: Optional[_Product] = None
        best_score = -math.inf

        for product in products:
            brand_score = (
                1.0
                if product.normalized_brand == normalized_brand
                else _token_similarity(product.normalized_brand, normalized_brand)
            )
            if brand_score < min_score and product.normalized_brand != normalized_brand:
                continue

            title_score = _title_similarity(product.normalized_title, normalized_title)
            if title_score < min_score:
                continue

            combined = 0.5 * brand_score + 0.5 * title_score
            if combined > best_score:
                best_score = combined
                best_product = product

        if best_product is None or best_score < min_score:
            return None

        return SearchResult(
            brand=best_product.brand,
            title=best_product.title,
            price=best_product.price,
            score=best_score,
        )

    def _fetch_products_from_web(self, brand: str, title: str) -> List[_Product]:
        """Open-web DuckDuckGo search with no domain restriction."""
        query = f"{brand} {title} price buy".strip()
        ddg_html = self._http_get_raw(
            f"{_DDGO_SEARCH_URL}?{urlencode({'q': query})}",
            self.timeout,
        )
        urls = _extract_ddg_urls(ddg_html)[: min(self.limit, _MAX_WEB_RESULTS)]
        products: List[_Product] = []
        fetched_pages = 0
        for url in urls:
            if len(products) >= self.limit:
                break
            try:
                page_html = self._http_get_raw(url, self.timeout)
                fetched_pages += 1
                domain = urlparse(url).netloc or url
                product = _extract_product_from_page(page_html, domain, brand)
                if product is not None:
                    products.append(product)
            except RemoteLookupError:
                continue
        if fetched_pages == 0:
            return []
        return products


def _title_similarity(candidate: str, query: str) -> float:
    token_score = _token_similarity(candidate, query)
    sequence_score = SequenceMatcher(None, candidate, query).ratio()
    return 0.7 * token_score + 0.3 * sequence_score


def _token_similarity(candidate: str, query: str) -> float:
    candidate_tokens = set(candidate.split())
    query_tokens = set(query.split())
    if not candidate_tokens or not query_tokens:
        return 0.0
    intersection = len(candidate_tokens & query_tokens)
    union = len(candidate_tokens | query_tokens)
    return intersection / union


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = " ".join(normalized.split())
    return normalized


def _default_http_get_raw(url: str, timeout: float) -> str:
    """Fetch a URL and return the response body as a string."""
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="ignore")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RemoteLookupError(str(exc)) from exc


def _extract_ddg_urls(html_content: str) -> List[str]:
    """Extract result page URLs from a DuckDuckGo HTML search response."""
    urls: List[str] = []
    # DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<url-encoded-url>
    for match in re.finditer(
        r'href="//duckduckgo\.com/l/\?(?:[^"]*&amp;|[^"]*&)?uddg=([^&"+])', html_content
    ):
        url = unquote(match.group(1))
        if url.startswith("http"):
            urls.append(url)
    if not urls:
        # Fallback: direct href on result anchors (some DDG layouts)
        for match in re.finditer(r'class="result__a"[^>]*href="(https?://[^"+])", html_content):
            urls.append(match.group(1))
    return urls


def _extract_product_from_page(html_content: str, domain: str, brand: str) -> Optional[_Product]:
    """Attempt to extract a Product from a product page's HTML.

    Tries schema.org JSON-LD first, then falls back to Open Graph meta tags.
    Returns ``None`` when no usable product information is found.
    """
    # --- JSON-LD ---
    for match in re.finditer(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html_content,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        product = _parse_jsonld_product(data, domain)
        if product is not None:
            return product

    # --- Open Graph / meta tag fallback ---
    return _extract_product_from_meta(html_content, domain, brand)


def _parse_jsonld_product(data: object, domain: str) -> Optional[_Product]:
    """Extract a Product from a schema.org JSON-LD object or graph."""
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        typ = item.get("@type", "")
        types: List[str] = typ if isinstance(typ, list) else [typ]
        if "Product" not in types:
            continue

        name = str(item.get("name", "")).strip()
        if not name:
            continue

        brand_field = item.get("brand", {})
        if isinstance(brand_field, dict):
            brand_name = str(brand_field.get("name", "")).strip()
        else:
            brand_name = str(brand_field).strip()

        price = _extract_price_from_offers(item.get("offers"))
        if price is None:
            continue

        return _Product(brand=brand_name or domain, title=name, price=price)
    return None


def _extract_price_from_offers(offers: object) -> Optional[float]:
    """Return a price float from a schema.org Offer or AggregateOffer structure."""
    if offers is None:
        return None
    if isinstance(offers, list):
        for offer in offers:
            price = _extract_price_from_offers(offer)
            if price is not None:
                return price
        return None
    if not isinstance(offers, dict):
        return None
    for field in ("price", "lowPrice"):
        value = offers.get(field)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def _extract_product_from_meta(html_content: str, domain: str, brand: str) -> Optional[_Product]:
    """Extract product info from Open Graph and similar meta tags."""
    og_title: Optional[str] = None
    og_price: Optional[float] = None

    m = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        html_content,
        re.IGNORECASE,
    )
    if m:
        og_title = m.group(1).strip()

    for pattern in (
        r'<meta[^>]+property=["\'](?:product|og):price:amount["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:data1["\'][^>]+content=["\']([^"\']+)',
    ):
        m = re.search(pattern, html_content, re.IGNORECASE)
        if m:
            try:
                # Strip common currency symbols before parsing
                raw = re.sub(r"[^\d.]+", "", m.group(1).strip())
                og_price = float(raw)
                break
            except (TypeError, ValueError):
                pass

    if og_title and og_price is not None:
        return _Product(brand=brand, title=og_title, price=og_price)
    return None

class RemoteLookupError(RuntimeError):
    """Raised when the remote price lookup fails."""


__all__ = [
    "ClothingPriceModel",
    "RemoteLookupError",
    "SearchResult",
]