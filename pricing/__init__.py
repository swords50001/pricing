"""Pricing lookup package."""

# Keep package import light-weight; avoid importing FastAPI app at package import time.
from .model import ClothingPriceModel, DEFAULT_DOMAINS, RemoteLookupError, SearchResult

__all__ = [
    "ClothingPriceModel",
    "DEFAULT_DOMAINS",
    "RemoteLookupError",
    "SearchResult",
]
