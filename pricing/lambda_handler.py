"""AWS Lambda handler that exposes the pricing lookup model."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .model import ClothingPriceModel, SearchResult

_DEFAULT_BASE_URL = "https://dummyjson.com/products/search"
_DEFAULT_LIMIT = 10
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_MIN_SCORE = 0.45


class _ModelFactory:
    """Callable wrapper used for dependency injection in tests."""

    def __call__(self, base_url: str, limit: int, timeout: float) -> ClothingPriceModel:
        return ClothingPriceModel(base_url=base_url, limit=limit, timeout=timeout)


_model_factory: _ModelFactory = _ModelFactory()


def handler(event: Optional[Dict[str, Any]], context: Any) -> Dict[str, Any]:
    """Entry point compatible with AWS Lambda container images.

    The event payload must contain a ``queries`` list with objects providing
    ``brand`` and ``title`` keys. Optional configuration keys ``base_url``,
    ``limit``, ``timeout`` and ``min_score`` override the default search
    settings.
    """

    if event is None:
        raise ValueError("Event payload must be a mapping containing a 'queries' list.")

    base_url = str(event.get("base_url", _DEFAULT_BASE_URL))
    limit = _coerce_int(event.get("limit", _DEFAULT_LIMIT), "limit")
    timeout = _coerce_float(event.get("timeout", _DEFAULT_TIMEOUT), "timeout")
    min_score = _coerce_float(event.get("min_score", _DEFAULT_MIN_SCORE), "min_score")

    queries_value = event.get("queries")
    if not isinstance(queries_value, list):
        raise ValueError("Event payload must supply 'queries' as a list of objects.")

    queries = _parse_queries(queries_value)

    model = _model_factory(base_url, limit, timeout)
    try:
        results = model.batch_search(queries, min_score=min_score)
    finally:
        model.close()

    return {"results": [_serialize_result(result) for result in results]}


def _parse_queries(items: Iterable[Any]) -> List[tuple[str, str]]:
    parsed: List[tuple[str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"Query at index {index} must be an object with 'brand' and 'title' keys.")
        brand = str(item.get("brand", "")).strip()
        title = str(item.get("title", "")).strip()
        if not brand or not title:
            raise ValueError(
                f"Query at index {index} must include non-empty 'brand' and 'title' values."
            )
        parsed.append((brand, title))
    if not parsed:
        raise ValueError("At least one query with brand and title must be provided.")
    return parsed


def _serialize_result(result: Optional[SearchResult]) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    return {
        "brand": result.brand,
        "title": result.title,
        "price": result.price,
        "score": result.score,
    }


def _coerce_int(value: Any, field: str) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"{field} must be an integer") from exc
    if candidate <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return candidate


def _coerce_float(value: Any, field: str) -> float:
    try:
        candidate = float(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"{field} must be a number") from exc
    if candidate <= 0:
        raise ValueError(f"{field} must be positive")
    return candidate


__all__ = ["handler"]
