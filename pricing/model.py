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
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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
    """Fetches clothing prices by querying a remote search endpoint."""

    def __init__(
        self,
        *,
        base_url: str = "https://dummyjson.com/products/search",
        limit: int = 10,
        timeout: float = 10.0,
        http_get: Optional[Callable[[str, Dict[str, str], float], Dict[str, object]]] = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url
        self.limit = limit
        self.timeout = timeout
        self._http_get = http_get or _default_http_get

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
    request = Request(target)
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


class RemoteLookupError(RuntimeError):
    """Raised when the remote price lookup fails."""


__all__ = [
    "ClothingPriceModel",
    "RemoteLookupError",
    "SearchResult",
]
