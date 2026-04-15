"""Utilities for discovering clothing prices from online product search APIs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple
import json
import math
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

# Default store domains used when no explicit domain list is provided.
DEFAULT_DOMAINS: List[str] = [
    "www.nordstrom.com",
    "www.zappos.com",
    "www.macys.com",
    "www.gap.com",
    "www.uniqlo.com",
]

_DDGO_SEARCH_URL = "https://html.duckduckgo.com/html/"
_MAX_RESULTS_PER_DOMAIN = 3  # maximum product pages inspected per domain per query
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
    """Internal representation of a product returned by the remote API."""

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
    """Fetches clothing prices by querying remote product search sources.

    Two search strategies are supported:

    * **Base-URL mode** (legacy): a single JSON API endpoint is queried with
      ``q`` and ``limit`` parameters and must return a ``products`` list
      containing ``brand``, ``title`` and ``price`` fields.
    * **Domain mode**: DuckDuckGo HTML search is used to find product pages on
      each domain in ``domains``.  Prices are extracted from schema.org
      JSON-LD or ``<meta>`` tags on the discovered pages.  Errors from any
      single domain are silently skipped.

    When ``domains`` is ``None`` the model falls back to base-URL mode.
    Pass ``domains=DEFAULT_DOMAINS`` (or any non-empty list) to activate
    domain-based search.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://dummyjson.com/products/search",
        domains: Optional[List[str]] = None,
        limit: int = 10,
        timeout: float = 10.0,
        http_get: Optional[Callable[[str, Dict[str, str], float], Dict[str, object]]] = None,
        http_get_raw: Optional[Callable[[str, float], str]] = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url
        self.domains = domains
        self.limit = limit
        self.timeout = timeout
        self._http_get = http_get or _default_http_get
        self._http_get_raw = http_get_raw or _default_http_get_raw

    def close(self) -> None:
        """Provided for API compatibility; no persistent connections are kept."""
        # The default implementation uses one-off HTTP requests and does not
        # maintain persistent connections, so there is nothing to close.
        return None

    def batch_search(
        self,
        queries: Iterable[Tuple[str, str]],
        *,
        min_score: float = 0.45,
    ) -> List[Optional[SearchResult]]:
        """Search for prices for a set of brand/title queries via the remote API."""

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
            products = self._fetch_products(brand, title)
        except RemoteLookupError:
            return None

        best_product: Optional[_Product] = None
        best_score = -math.inf

        for product in products:
            brand_score = 1.0 if product.normalized_brand == normalized_brand else _token_similarity(
                product.normalized_brand, normalized_brand
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

    def _fetch_products(self, brand: str, title: str) -> List[_Product]:
        if self.domains is not None:
            return self._fetch_products_from_domains(brand, title)
        # Legacy base-URL mode
        query = f"{brand} {title}".strip()
        payload = self._http_get(
            self.base_url,
            {"q": query, "limit": str(self.limit)},
            self.timeout,
        )
        products = payload.get("products", []) if isinstance(payload, dict) else []
        normalised_products: List[_Product] = []
        for product in products:
            if not isinstance(product, dict):
                continue
            brand_value = str(product.get("brand", "")).strip()
            title_value = str(product.get("title", "")).strip()
            price_value = product.get("price")
            if not brand_value or not title_value or price_value is None:
                continue
            try:
                price = float(price_value)
            except (TypeError, ValueError):
                continue
            normalised_products.append(_Product(brand=brand_value, title=title_value, price=price))
        return normalised_products

    def _fetch_products_from_domains(self, brand: str, title: str) -> List[_Product]:
        """Collect product candidates by searching each domain via DuckDuckGo."""
        products: List[_Product] = []
        query = f"{brand} {title}".strip()
        for domain in self.domains:  # type: ignore[union-attr]
            if len(products) >= self.limit:
                break
            try:
                domain_products = self._search_domain(domain, brand, query)
                products.extend(domain_products)
            except RemoteLookupError:
                continue
        return products[: self.limit]

    def _search_domain(self, domain: str, brand: str, query: str) -> List[_Product]:
        """Use DuckDuckGo to discover product pages on *domain*, then extract prices."""
        ddg_query = f"{query} site:{domain}"
        ddg_html = self._http_get_raw(
            f"{_DDGO_SEARCH_URL}?{urlencode({'q': ddg_query})}",
            self.timeout,
        )
        urls = _extract_ddg_urls(ddg_html)[:_MAX_RESULTS_PER_DOMAIN]
        products: List[_Product] = []
        for url in urls:
            try:
                page_html = self._http_get_raw(url, self.timeout)
                product = _extract_product_from_page(page_html, domain, brand)
                if product is not None:
                    products.append(product)
            except RemoteLookupError:
                continue
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


def _default_http_get(url: str, params: Dict[str, str], timeout: float) -> Dict[str, object]:
    query = urlencode(params)
    target = f"{url}?{query}" if query else url
    request = Request(target, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            data = response.read().decode(charset, errors="ignore")
    except (HTTPError, URLError) as exc:
        raise RemoteLookupError(str(exc)) from exc
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise RemoteLookupError("Failed to decode JSON payload") from exc


def _default_http_get_raw(url: str, timeout: float) -> str:
    """Fetch a URL and return the response body as a string."""
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="ignore")
    except (HTTPError, URLError) as exc:
        raise RemoteLookupError(str(exc)) from exc


def _extract_ddg_urls(html_content: str) -> List[str]:
    """Extract result page URLs from a DuckDuckGo HTML search response."""
    urls: List[str] = []
    # DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<url-encoded-url>
    for match in re.finditer(
        r'href="//duckduckgo\.com/l/\?(?:[^"]*&amp;|[^"]*&)?uddg=([^"&]+)', html_content
    ):
        url = unquote(match.group(1))
        if url.startswith("http"):
            urls.append(url)
    if not urls:
        # Fallback: direct href on result anchors (some DDG layouts)
        for match in re.finditer(
            r'class="result__a"[^>]*href="(https?://[^"]+)"', html_content
        ):
            urls.append(match.group(1))
    return urls


def _extract_product_from_page(html_content: str, domain: str, brand: str) -> Optional["_Product"]:
    """Attempt to extract a :class:`_Product` from a product page's HTML.

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


def _parse_jsonld_product(data: object, domain: str) -> Optional["_Product"]:
    """Extract a :class:`_Product` from a schema.org JSON-LD object or graph."""
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


def _extract_product_from_meta(html_content: str, domain: str, brand: str) -> Optional["_Product"]:
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
                raw = re.sub(r"[^\d.]", "", m.group(1).strip())
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
    "DEFAULT_DOMAINS",
    "RemoteLookupError",
    "SearchResult",
]
